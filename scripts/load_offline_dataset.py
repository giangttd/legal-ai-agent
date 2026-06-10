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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = build_db_config()
    snap = args.dataset_dir or resolve_snapshot()
    print(f"[load] dataset snapshot: {snap}")
    print(f"[load] DB host={cfg['host']} db={cfg['dbname']} user={cfg['user']} "
          f"sslmode={cfg['sslmode']}")  # never print password
    # Phases wired in Tasks 8-11.
    raise SystemExit("phases not yet implemented")


if __name__ == "__main__":
    sys.exit(main())
