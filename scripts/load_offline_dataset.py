#!/usr/bin/env python3
"""Bulk-load the offline HuggingFace Vietnamese legal dataset into Postgres.

Run on the HOST against the docker-compose Postgres (see README / spec §5.3):

    export SUPABASE_DB_HOST=localhost
    export SUPABASE_DB_PORT=${DB_PORT:-5432}
    export DB_NAME=${POSTGRES_DB:-legalai}
    export DB_USER=${POSTGRES_USER:-legalai}
    export SUPABASE_DB_PASSWORD=${POSTGRES_PASSWORD:-legalai2026}
    export DB_SSL_MODE=disable
    python scripts/load_offline_dataset.py --truncate
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid

import psycopg2
import pyarrow.parquet as pq
from psycopg2.extras import execute_values

from scripts import _law_ingest as ing

DEFAULT_DATASET_ROOT = "data/datasets-legal-docs"
CANONICAL_SEARCH_MIGRATION = "scripts/migration_search_v5_fixed.sql"
MIN_CONTENT_CHARS = 100
SOURCE_SITE = "huggingface/th1nhng0"
COMMIT_EVERY = 2000


def build_db_config() -> dict:
    """Same env-var names as src/api/main.py:DB_CONFIG (spec §5.3)."""
    return {
        "host": os.getenv("SUPABASE_DB_HOST", "localhost"),
        "port": int(os.getenv("SUPABASE_DB_PORT", "5432")),
        "dbname": os.getenv("DB_NAME", "postgres"),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("SUPABASE_DB_PASSWORD", ""),
        "sslmode": os.getenv("DB_SSL_MODE", "require"),
    }


def resolve_snapshot(dataset_root: str = DEFAULT_DATASET_ROOT) -> str:
    """Return the newest HuggingFace snapshot dir that contains data/metadata.parquet."""
    snaps_dir = os.path.join(dataset_root, "snapshots")
    if not os.path.isdir(snaps_dir):
        raise FileNotFoundError(f"No snapshots dir at {snaps_dir}")
    candidates = []
    for name in os.listdir(snaps_dir):
        path = os.path.join(snaps_dir, name)
        if os.path.isfile(os.path.join(path, "data", "metadata.parquet")):
            candidates.append(path)
    if not candidates:
        raise FileNotFoundError(f"No usable snapshot under {snaps_dir}")
    return max(candidates, key=os.path.getmtime)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Load offline legal dataset into Postgres")
    p.add_argument("--dataset-dir", default=None,
                   help="Override snapshot dir (default: newest under data/datasets-legal-docs)")
    p.add_argument("--truncate", action="store_true",
                   help="TRUNCATE law_* before load (required to overwrite)")
    p.add_argument("--limit", type=int, default=None, help="Cap docs per config (validation)")
    p.add_argument("--skip-relations", action="store_true")
    p.add_argument("--skip-legacy", action="store_true")
    return p.parse_args(argv)


TSV_INDEXES = {
    "idx_law_documents_tsv": "CREATE INDEX IF NOT EXISTS idx_law_documents_tsv "
                             "ON law_documents USING GIN(tsv)",
    "idx_law_chunks_tsv": "CREATE INDEX IF NOT EXISTS idx_law_chunks_tsv "
                          "ON law_chunks USING GIN(tsv)",
}


def reconcile_schema(conn) -> None:
    """Make the DB match the search contract: pg_trgm, domains legal_domain[],
    and the canonical search_law() function (spec §4a). Idempotent."""
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        for table in ("law_documents", "law_chunks"):
            cur.execute(
                "SELECT udt_name FROM information_schema.columns "
                "WHERE table_name=%s AND column_name='domains'", (table,))
            row = cur.fetchone()
            # udt_name for an array of enum `legal_domain` is `_legal_domain`.
            if row and row[0] != "_legal_domain":
                print(f"[load] altering {table}.domains -> legal_domain[]")
                cur.execute(
                    f"ALTER TABLE {table} ALTER COLUMN domains "
                    f"TYPE legal_domain[] USING domains::legal_domain[]")
        with open(CANONICAL_SEARCH_MIGRATION, encoding="utf-8") as f:
            cur.execute(f.read())
    conn.commit()
    print("[load] schema reconciled (pg_trgm, legal_domain[], search_law())")


def truncate_tables(conn) -> None:
    """Empty the law_* tables. Run BEFORE reconcile_schema so the
    `ALTER COLUMN domains TYPE legal_domain[] USING domains::legal_domain[]` runs
    on empty tables and can never fail on a pre-existing non-enum value (review #2)."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE law_chunks, law_relations, law_documents")
    conn.commit()
    print("[load] truncated law_chunks, law_relations, law_documents")


def drop_tsv_indexes(conn) -> None:
    """Drop the tsv GIN indexes for bulk-insert speed. Called AFTER
    reconcile_schema (which re-creates them via the search migration)."""
    with conn.cursor() as cur:
        for name in TSV_INDEXES:
            cur.execute(f"DROP INDEX IF EXISTS {name}")
    conn.commit()
    print("[load] dropped tsv GIN indexes for bulk load")


def recreate_indexes(conn) -> None:
    with conn.cursor() as cur:
        for ddl in TSV_INDEXES.values():
            cur.execute(ddl)
        cur.execute("ANALYZE law_documents")
        cur.execute("ANALYZE law_chunks")
        cur.execute("ANALYZE law_relations")
    conn.commit()
    print("[load] recreated tsv GIN indexes + ANALYZE")


def assert_empty_or_die(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM law_documents")
        n = cur.fetchone()[0]
    if n:
        raise SystemExit(
            f"law_documents has {n} rows. Re-run with --truncate to overwrite.")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = build_db_config()
    snap = args.dataset_dir or resolve_snapshot()
    print(f"[load] dataset snapshot: {snap}")
    print(f"[load] DB host={cfg['host']} db={cfg['dbname']} user={cfg['user']} "
          f"sslmode={cfg['sslmode']}")  # never print password
    conn = psycopg2.connect(**cfg)  # outside try: a connect failure must not enter finally
    try:
        # Order matters (review #2): empty the tables BEFORE reconcile_schema's
        # ALTER, so `USING domains::legal_domain[]` runs on empty tables and can
        # never abort on a pre-existing non-enum value.
        if args.truncate:
            truncate_tables(conn)
        else:
            assert_empty_or_die(conn)
        reconcile_schema(conn)   # ALTER domains, pg_trgm, deploy search_law() + indexes
        drop_tsv_indexes(conn)   # drop the tsv GIN the migration just created, for bulk speed
        # --- Phase 1 (Task 9): uncomment ---
        # id_map, seen = load_current(conn, snap, args.limit)
        # --- Phase 2 (Task 10): uncomment ---
        # if not args.skip_legacy:
        #     load_legacy(conn, snap, seen, args.limit)
        # --- Phase 3 + finalize (Task 11): uncomment ---
        # if not args.skip_relations:
        #     load_relationships(conn, snap, id_map)
        # recreate_indexes(conn)
        # verify(conn, args.limit, args.skip_relations)
    except Exception:
        conn.rollback()  # clear any aborted-transaction state before re-raising
        raise
    finally:
        # Crash-safe (review #1, #7): rebuild the tsv GIN indexes even after a
        # failure. rollback() first so this runs on a CLEAN transaction — a mid-load
        # error otherwise leaves the txn aborted and CREATE INDEX would fail with
        # "current transaction is aborted". Best-effort; never mask the real error.
        try:
            conn.rollback()
            recreate_indexes(conn)
        except Exception as cleanup_err:  # noqa: BLE001
            print(f"[load] WARNING: index rebuild during cleanup failed: {cleanup_err}")
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
