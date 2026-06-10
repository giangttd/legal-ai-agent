import os

import pytest

from scripts.load_offline_dataset import build_db_config, resolve_snapshot


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
