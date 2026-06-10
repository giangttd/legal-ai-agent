#!/usr/bin/env python3
"""Bulk-load the offline HuggingFace Vietnamese legal dataset into Postgres.

Run on the HOST against the docker-compose Postgres (see README / spec §5.3):

    export SUPABASE_DB_HOST=localhost
    export SUPABASE_DB_PORT=${DB_PORT:-5432}
    export DB_NAME=${POSTGRES_DB:-legalai}
    export DB_USER=${POSTGRES_USER:-legalai}
    export SUPABASE_DB_PASSWORD=${POSTGRES_PASSWORD:-legalai2026}
    export DB_SSL_MODE=disable
    # Run as a MODULE from the repo root (the `from scripts import ...` import
    # only resolves this way — running the file by path raises ModuleNotFoundError):
    python3 -m scripts.load_offline_dataset --truncate --skip-legacy
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
        # Ensure the admin-law label exists (review #4) so the live search path
        # (main.py:detect_domain emits 'hanh_chinh') doesn't 500 on the
        # ::legal_domain[] cast. Commit it before any use — a new enum value
        # cannot be used in the same transaction it was added in.
        cur.execute("ALTER TYPE legal_domain ADD VALUE IF NOT EXISTS 'hanh_chinh';")
    conn.commit()
    with conn.cursor() as cur:
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


_DOC_COLS = ("id", "title", "law_number", "law_type", "issuer", "signer",
             "issued_date", "effective_date", "expiry_date", "status",
             "domains", "full_text", "source_site", "source_url",
             "article_count", "word_count")

# The `domains` placeholder is cast to legal_domain[] — psycopg2 renders a Python
# list as ARRAY[...] (text[]), which Postgres will NOT implicitly coerce to the
# legal_domain[] column type in an INSERT, so the cast is required (found at deploy).
def _placeholder(col: str) -> str:
    return "%s::legal_domain[]" if col == "domains" else "%s"


# Trailing two %s (title, full_text) feed the tsv expression (review #6 coalesce).
_DOC_TEMPLATE = ("(" + ",".join(_placeholder(c) for c in _DOC_COLS) +
                 ", to_tsvector('simple', coalesce(%s,'') || ' ' || coalesce(%s,'')))")

_CHUNK_COLS = ("law_id", "article", "clause", "title", "content", "domains")
_CHUNK_TEMPLATE = ("(" + ",".join(_placeholder(c) for c in _CHUNK_COLS) +
                   ", to_tsvector('simple', %s))")  # trailing %s = content


def _flush_docs(cur, rows: list[tuple]) -> None:
    if not rows:
        return
    execute_values(
        cur,
        f"INSERT INTO law_documents ({','.join(_DOC_COLS)}, tsv) VALUES %s",
        rows, template=_DOC_TEMPLATE, page_size=1000)


def _flush_chunks(cur, rows: list[tuple]) -> None:
    if not rows:
        return
    execute_values(
        cur,
        f"INSERT INTO law_chunks ({','.join(_CHUNK_COLS)}, tsv) VALUES %s",
        rows, template=_CHUNK_TEMPLATE, page_size=1000)


def _group_content_by_id(content_path: str) -> dict[int, str]:
    """Group content rows by int(id), dedup byte-identical fragments, concat in
    file row order (review #8). content.id is a numeric string."""
    table = pq.read_table(content_path, columns=["id", "content_html"])
    ids = table.column("id").to_pylist()
    htmls = table.column("content_html").to_pylist()
    grouped: dict[int, list[str]] = {}
    for raw_id, html in zip(ids, htmls):
        if raw_id is None or not str(raw_id).strip().lstrip("-").isdigit():
            continue
        key = int(raw_id)
        frag = html or ""
        bucket = grouped.setdefault(key, [])
        if frag and frag not in bucket:  # dedup exact-duplicate fragments
            bucket.append(frag)
    return {k: "\n".join(v) for k, v in grouped.items()}


def load_current(conn, snap: str, limit: int | None):
    """Load the current config. Returns (id_map: dict[int,uuid], seen_numbers: set)."""
    meta_path = os.path.join(snap, "data", "metadata.parquet")
    content_path = os.path.join(snap, "data", "content.parquet")

    meta_tbl = pq.read_table(meta_path)
    meta_by_id = {r["id"]: r for r in meta_tbl.to_pylist()}
    print(f"[load] current metadata rows: {len(meta_by_id)}")

    content_by_id = _group_content_by_id(content_path)
    print(f"[load] current unique content docs: {len(content_by_id)}")

    id_map: dict[int, str] = {}
    seen_numbers: set[str] = set()
    doc_rows: list[tuple] = []
    chunk_rows: list[tuple] = []
    loaded = 0

    cur = conn.cursor()
    for int_id, html in content_by_id.items():
        if limit is not None and loaded >= limit:
            break
        text = ing.clean_html(html)
        if len(text) < MIN_CONTENT_CHARS:
            continue
        meta = meta_by_id.get(int_id)
        doc_uuid = str(uuid.uuid4())
        if meta:
            title = (meta.get("title") or "").strip() or f"Văn bản VBPL-{int_id}"
            law_number = (meta.get("so_ky_hieu") or "").strip() or f"VBPL-{int_id}"
            law_type = ing.map_law_type_vi(meta.get("loai_van_ban"))
            issuer = (meta.get("co_quan_ban_hanh") or "").strip() or "Chưa xác định"
            signer = meta.get("nguoi_ky")
            issued = ing.parse_date_vi(meta.get("ngay_ban_hanh"))
            effective = ing.parse_date_vi(meta.get("ngay_co_hieu_luc"))
            expiry = ing.parse_date_vi(meta.get("ngay_het_hieu_luc"))
            status = ing.map_status_vi(meta.get("tinh_trang_hieu_luc"))
        else:
            # content-only stub: title is NOT NULL (review #4)
            first_line = next((ln for ln in text.splitlines() if ln.strip()), "")
            title = first_line[:300] or f"Văn bản VBPL-{int_id}"
            law_number = f"VBPL-{int_id}"
            law_type, issuer, signer = "other", "Chưa xác định", None
            issued = effective = expiry = None
            status = "active"
        domains = ing.detect_domains(title, text)

        id_map[int_id] = doc_uuid
        seen_numbers.add(ing.normalize_doc_number(law_number))
        article_count = ing.count_articles(text)
        doc_rows.append((
            doc_uuid, title, law_number, law_type, issuer, signer,
            issued, effective, expiry, status, domains, text,
            SOURCE_SITE, f"https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID={int_id}",
            article_count, len(text.split()),
            title, text,  # tsv operands
        ))
        for ch in ing.chunk_document(text):
            chunk_rows.append((
                doc_uuid, ch["article"], ch["clause"], ch["title"],
                ch["content"], domains, ch["content"],  # last = tsv operand
            ))
        loaded += 1
        if loaded % COMMIT_EVERY == 0:
            _flush_docs(cur, doc_rows); _flush_chunks(cur, chunk_rows)
            conn.commit(); doc_rows.clear(); chunk_rows.clear()
            print(f"[load] current progress: {loaded} docs")

    _flush_docs(cur, doc_rows); _flush_chunks(cur, chunk_rows)
    conn.commit(); cur.close()
    print(f"[load] current done: {loaded} docs")
    return id_map, seen_numbers


def _group_legacy_content(content_path: str) -> dict[int, str]:
    table = pq.read_table(content_path, columns=["id", "content"])
    ids = table.column("id").to_pylist()
    texts = table.column("content").to_pylist()
    grouped: dict[int, list[str]] = {}
    for rid, txt in zip(ids, texts):
        # Mirror the digit guard from _group_content_by_id (review #5): legacy id
        # is int64 today, but guard defensively so a stray non-numeric id can never
        # crash int() and abort the whole load.
        if rid is None or not str(rid).strip().lstrip("-").isdigit():
            continue
        frag = txt or ""
        bucket = grouped.setdefault(int(rid), [])
        if frag and frag not in bucket:
            bucket.append(frag)
    return {k: "\n".join(v) for k, v in grouped.items()}


def load_legacy(conn, snap: str, seen_numbers: set[str], limit: int | None) -> None:
    """Load legacy docs whose official number is not already present."""
    meta_path = os.path.join(snap, "legacy", "metadata.parquet")
    content_path = os.path.join(snap, "legacy", "content.parquet")
    if not os.path.isfile(meta_path):
        print("[load] no legacy config found; skipping")
        return

    # legacy/content.parquet is ~10GB uncompressed in a SINGLE row group, so the
    # read below materializes it whole — needs ~20GB+ RAM (review #2). Skip with
    # --skip-legacy on memory-constrained machines.
    print("[load] WARNING: loading legacy content (~10GB uncompressed, single row "
          "group) — needs ~20GB+ RAM. Use --skip-legacy if this OOMs.")
    meta_by_id = {r["id"]: r for r in pq.read_table(meta_path).to_pylist()}
    content_by_id = _group_legacy_content(content_path)
    print(f"[load] legacy metadata={len(meta_by_id)} content={len(content_by_id)}")

    doc_rows: list[tuple] = []
    chunk_rows: list[tuple] = []
    loaded = skipped_dup = 0
    cur = conn.cursor()
    for int_id, text in content_by_id.items():
        if limit is not None and loaded >= limit:
            break
        if len(text) < MIN_CONTENT_CHARS:
            continue
        meta = meta_by_id.get(int_id)
        if not meta:
            continue
        number = (meta.get("document_number") or "").strip()
        norm = ing.normalize_doc_number(number)
        if norm and norm in seen_numbers:
            skipped_dup += 1
            continue
        seen_numbers.add(norm)
        title = (meta.get("title") or "").strip() or f"VB-{int_id}"
        law_number = number or f"LEGACY-{int_id}"
        law_type = ing.map_law_type_legacy(meta.get("legal_type"))
        issuer = (meta.get("issuing_authority") or "").strip() or "Chưa xác định"
        signer = meta.get("signers")
        issued = ing.parse_date_vi(meta.get("issuance_date"))
        effective = ing.parse_date_vi(meta.get("effect_date"))
        expiry = ing.parse_date_vi(meta.get("effectless_date"))
        status = ing.map_status_legacy(meta.get("effect_status"))
        domains = ing.detect_domains(title, text)
        doc_uuid = str(uuid.uuid4())
        doc_rows.append((
            doc_uuid, title, law_number, law_type, issuer, signer,
            issued, effective, expiry, status, domains, text,
            "huggingface/th1nhng0-legacy", f"https://vbpl.vn/legacy/{int_id}",
            ing.count_articles(text), len(text.split()), title, text,
        ))
        for ch in ing.chunk_document(text):
            chunk_rows.append((
                doc_uuid, ch["article"], ch["clause"], ch["title"],
                ch["content"], domains, ch["content"],
            ))
        loaded += 1
        if loaded % COMMIT_EVERY == 0:
            _flush_docs(cur, doc_rows); _flush_chunks(cur, chunk_rows)
            conn.commit(); doc_rows.clear(); chunk_rows.clear()
            print(f"[load] legacy progress: {loaded} docs ({skipped_dup} dup-skipped)")

    _flush_docs(cur, doc_rows); _flush_chunks(cur, chunk_rows)
    conn.commit(); cur.close()
    print(f"[load] legacy done: {loaded} docs ({skipped_dup} dup-skipped)")


def load_relationships(conn, snap: str, id_map: dict[int, str]) -> None:
    """Insert edges where BOTH endpoints resolved to a current-config UUID.
    Legacy ids never enter id_map, so there is no cross-space collision."""
    rel_path = os.path.join(snap, "data", "relationships.parquet")
    table = pq.read_table(rel_path)
    doc_ids = table.column("doc_id").to_pylist()
    other_ids = table.column("other_doc_id").to_pylist()
    rels = table.column("relationship").to_pylist()

    rows: list[tuple] = []
    inserted = skipped = 0
    cur = conn.cursor()
    for d, o, rel in zip(doc_ids, other_ids, rels):
        s = id_map.get(d)
        t = id_map.get(o)
        if not s or not t:
            skipped += 1
            continue
        rows.append((s, t, rel))
        if len(rows) >= 5000:
            execute_values(
                cur,
                "INSERT INTO law_relations (source_law_id, target_law_id, relation_type) VALUES %s",
                rows, page_size=1000)
            conn.commit()
            inserted += len(rows); rows.clear()
    if rows:
        execute_values(
            cur,
            "INSERT INTO law_relations (source_law_id, target_law_id, relation_type) VALUES %s",
            rows, page_size=1000)
        conn.commit()
        inserted += len(rows)
    cur.close()
    print(f"[load] relationships: {inserted} inserted, {skipped} dangling-skipped")


def verify(conn, limit: int | None, skip_relations: bool) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM law_documents")
        docs = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM law_chunks")
        chunks = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM law_relations")
        rels = cur.fetchone()[0]
        print(f"[verify] docs={docs} chunks={chunks} relations={rels}")
        assert docs > 0 and chunks > 0, "no docs/chunks loaded"

        # Schema-agnostic tsv sanity — proves tsv populated, no search_law() needed.
        cur.execute(
            "SELECT count(*) FROM law_chunks WHERE tsv @@ to_tsquery('simple', %s)",
            ("lao & động",))
        tsv_hits = cur.fetchone()[0]
        print(f"[verify] tsv 'lao động' hits: {tsv_hits}")

        # End-to-end via the live search_law() function.
        cur.execute("SELECT count(*) FROM search_law(%s, NULL, %s)",
                    ("hợp đồng lao động", 10))
        sl_hits = cur.fetchone()[0]
        print(f"[verify] search_law('hợp đồng lao động') rows: {sl_hits}")

        if limit is None:
            assert tsv_hits > 0, "tsv populated but no keyword match — check to_tsvector"
            assert sl_hits > 0, "search_law returned nothing"
            if not skip_relations:
                assert rels > 0, "expected relationships on a full run"
        else:
            print("[verify] keyword/search_law/relationship assertions skipped (--limit active)")
    print("[verify] OK")


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
        id_map, seen = load_current(conn, snap, args.limit)
        if not args.skip_legacy:
            load_legacy(conn, snap, seen, args.limit)
        if not args.skip_relations:
            load_relationships(conn, snap, id_map)
        recreate_indexes(conn)          # rebuild tsv GIN indexes on success
        verify(conn, args.limit, args.skip_relations)  # post-load sanity checks
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
