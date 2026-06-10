import os

import psycopg2
import pytest

from scripts.load_offline_dataset import build_db_config, main, resolve_snapshot


def test_resolve_snapshot_picks_newest(tmp_path):
    import time

    base = tmp_path / "data" / "datasets-legal-docs" / "snapshots"
    old = base / "aaa"
    new = base / "bbb"
    for d in (old, new):
        (d / "data").mkdir(parents=True)
        (d / "data" / "metadata.parquet").write_bytes(b"x")
    now = time.time()
    os.utime(old, (now - 1000, now - 1000))  # `old` clearly older
    os.utime(new, (now + 1000, now + 1000))  # `new` clearly newer
    snap = resolve_snapshot(str(base.parent))
    assert snap.endswith("bbb")


def test_resolve_snapshot_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_snapshot(str(tmp_path / "nope"))


def test_build_db_config_reads_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_HOST", "localhost")
    monkeypatch.setenv("DB_NAME", "legalai")
    monkeypatch.setenv("DB_USER", "legalai")
    monkeypatch.setenv("DB_SSL_MODE", "disable")
    cfg = build_db_config()
    assert cfg["host"] == "localhost"
    assert cfg["dbname"] == "legalai"
    assert cfg["sslmode"] == "disable"


def _db_available() -> bool:
    try:
        conn = psycopg2.connect(**build_db_config())
        conn.close()
        return True
    except Exception:
        return False


def _dataset_available() -> bool:
    try:
        resolve_snapshot()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_available() or not _dataset_available(),
                    reason="needs live Docker DB + dataset snapshot")
def test_loader_end_to_end_subset():
    # Small capped run: do NOT assert a specific keyword hit (a 50-doc sample may
    # contain no labor-law document — that would false-fail). Assert the loader
    # succeeds, rows + tsv are populated, and search_law() is callable.
    rc = main(["--truncate", "--limit", "50", "--skip-relations"])
    assert rc == 0
    conn = psycopg2.connect(**build_db_config())
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM law_documents")
        assert cur.fetchone()[0] > 0
        cur.execute("SELECT count(*) FROM law_chunks WHERE tsv IS NOT NULL")
        assert cur.fetchone()[0] > 0
        # search_law must be deployed and callable (returns a count >= 0, no error)
        cur.execute("SELECT count(*) FROM search_law(%s, NULL, %s)", ("lao động", 5))
        assert cur.fetchone()[0] >= 0
    conn.close()
