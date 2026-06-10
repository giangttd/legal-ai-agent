# Offline Legal Dataset Loader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bulk-load the offline HuggingFace Vietnamese legal dataset (`data/datasets-legal-docs/`) into the Postgres `law_documents` / `law_chunks` / `law_relations` tables so legal search works without web crawling.

**Architecture:** A single streaming Python loader (`scripts/load_offline_dataset.py`) backed by pure, unit-tested helpers (`scripts/_law_ingest.py`). It reconciles the Docker schema to the search contract (`legal_domain[]`, `pg_trgm`, `search_law()`), merges the `current` (HTML, rich metadata) and `legacy` (plaintext, broader) dataset configs with dedup on official document number, populates `tsv` for keyword search, and remaps integer dataset ids to UUIDs for the relationships graph.

**Tech Stack:** Python 3.10+, psycopg2, pyarrow (parquet), BeautifulSoup4 + lxml (HTML→text), Postgres 15 + pgvector (Docker), pytest.

**Spec:** `docs/superpowers/specs/2026-06-10-offline-legal-dataset-loader-design.md` (commit `9abf939`). All 8 x-review findings are folded into the spec and reflected below.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `requirements.txt` | Modify | Add `pyarrow`, `beautifulsoup4`, `lxml` |
| `.gitignore` | Modify | Exclude the 400M+ dataset snapshot + json dumps |
| `.dockerignore` | Create | Keep the dataset out of the app image |
| `database/init.sql` | Modify | Root-fix schema for fresh DBs: `domains legal_domain[]` + `pg_trgm` |
| `scripts/_law_ingest.py` | Create | Pure helpers: HTML clean, chunk, domain detect, enum maps, number/date parse |
| `tests/test_law_ingest.py` | Create | Unit tests for every helper |
| `scripts/load_offline_dataset.py` | Create | CLI orchestrator: connect, reconcile, load current/legacy/relations, verify |
| `tests/test_load_offline_smoke.py` | Create | DB-gated integration smoke test |
| `README.md` | Modify | Document the offline-load path + host env block |

**Canonical search migration** `scripts/migration_search_v5_fixed.sql` already exists and is applied verbatim by the loader — not edited.

---

## Task 1: Dependencies and ignore files

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Create: `.dockerignore`

- [ ] **Step 1: Add parquet + HTML deps to `requirements.txt`**

Append these lines to `requirements.txt`:

```
# Offline legal dataset loader
pyarrow>=15.0.0
beautifulsoup4>=4.12.0
lxml>=5.0.0
```

- [ ] **Step 2: Exclude the dataset from git**

Append to `.gitignore`:

```
# Offline legal dataset (400MB+ HuggingFace snapshot — never commit)
data/datasets-legal-docs/
data/*.json
```

- [ ] **Step 3: Create `.dockerignore`**

The `Dockerfile` does `COPY data/ ./data/`; without this the image would bloat by multiple GB. Create `.dockerignore`:

```
# Keep the offline dataset out of the app image (loader runs on host)
data/datasets-legal-docs/
data/*.json
.git/
__pycache__/
*.pyc
.pytest_cache/
.venv/
tests/
```

- [ ] **Step 4: Verify deps install**

Run: `pip install -r requirements.txt`
Expected: installs `pyarrow`, `beautifulsoup4`, `lxml` with no error.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .gitignore .dockerignore
git commit -m "chore: add offline-dataset loader deps + ignore the snapshot"
```

---

## Task 2: Schema root-fix in `database/init.sql`

Fresh Docker DBs must be created with `domains legal_domain[]` and `pg_trgm` so `search_law()` works. (Existing/truncated DBs are handled idempotently by the loader in Task 8.)

**Files:**
- Modify: `database/init.sql:10` (extensions), `:283` (law_chunks.domains), `:302` (law_documents.domains)

- [ ] **Step 1: Add the `pg_trgm` extension**

In `database/init.sql`, after the line `CREATE EXTENSION IF NOT EXISTS "vector";` add:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

- [ ] **Step 2: Change `law_chunks.domains` to the enum array**

In the `law_chunks` table (around line 283), change:

```sql
    domains TEXT[],
```
to:
```sql
    domains legal_domain[],
```

- [ ] **Step 3: Change `law_documents.domains` to the enum array**

In the `law_documents` table (around line 302), change:

```sql
    domains TEXT[] NOT NULL,
```
to:
```sql
    domains legal_domain[] NOT NULL,
```

- [ ] **Step 4: Verify the edits**

Run: `grep -nE "pg_trgm|domains (legal_domain|TEXT)\[\]" database/init.sql`
Expected: `pg_trgm` present; both `domains` lines now read `legal_domain[]`; no `domains TEXT[]` remains.

- [ ] **Step 5: Commit**

```bash
git add database/init.sql
git commit -m "fix: init.sql domains -> legal_domain[] + pg_trgm for search_law()"
```

---

## Task 3: `_law_ingest.py` — HTML cleaning

**Files:**
- Create: `scripts/_law_ingest.py`
- Test: `tests/test_law_ingest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_law_ingest.py`:

```python
from scripts._law_ingest import clean_html


def test_clean_html_strips_tags_and_scripts():
    html = (
        '<table class="detailcontent"><tr><td>'
        "<div>Điều 1. Phạm vi</div>"
        "<script>alert(1)</script><style>.x{}</style>"
        "<p>Nội dung điều luật.</p></td></tr></table>"
    )
    out = clean_html(html)
    assert "Điều 1. Phạm vi" in out
    assert "Nội dung điều luật." in out
    assert "alert" not in out
    assert "<" not in out


def test_clean_html_empty():
    assert clean_html("") == ""
    assert clean_html(None) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_law_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts._law_ingest'`

- [ ] **Step 3: Create the module with `clean_html`**

Create `scripts/_law_ingest.py`:

```python
"""Pure, unit-testable helpers for loading Vietnamese legal documents.

Shared by scripts/load_offline_dataset.py. No DB or network access here.
"""
from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup


def clean_html(html: str | None) -> str:
    """Convert raw legal-document HTML to normalized plaintext."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    joined = "\n".join(ln for ln in lines if ln)
    return re.sub(r"\n{3,}", "\n\n", joined)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_law_ingest.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/_law_ingest.py tests/test_law_ingest.py
git commit -m "feat: _law_ingest.clean_html with tests"
```

---

## Task 4: `_law_ingest.py` — chunking by article

**Files:**
- Modify: `scripts/_law_ingest.py`
- Test: `tests/test_law_ingest.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_law_ingest.py`:

```python
from scripts._law_ingest import chunk_document, simple_chunk


def test_simple_chunk_overlap_and_min_length():
    text = "x" * 4000
    chunks = simple_chunk(text, size=1500, overlap=200)
    assert len(chunks) >= 2
    assert all(len(c) > 50 for c in chunks)


def test_chunk_document_splits_by_article():
    text = "\n".join(f"Điều {i}. Tiêu đề {i}\nNội dung điều {i}." for i in range(1, 6))
    chunks = chunk_document(text)
    assert len(chunks) == 5
    assert chunks[0]["article"] == "Điều 1"
    assert "Nội dung điều 1." in chunks[0]["content"]


def test_chunk_document_no_articles_falls_back():
    text = "Một đoạn văn bản dài không có điều khoản. " * 60
    chunks = chunk_document(text)
    assert len(chunks) >= 1
    assert chunks[0]["article"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_law_ingest.py -v`
Expected: FAIL — `ImportError: cannot import name 'chunk_document'`

- [ ] **Step 3: Add chunking functions**

Append to `scripts/_law_ingest.py`:

```python
_ARTICLE_RE = re.compile(r"(?:^|\n)((?:Điều|ĐIỀU)\s+\d+[a-z]?\.?\s*[^\n]*)", re.MULTILINE)
_ARTNUM_RE = re.compile(r"(?:Điều|ĐIỀU)\s+(\d+[a-z]?)")


def simple_chunk(text: str, size: int = 1500, overlap: int = 200) -> list[str]:
    """Sliding-window chunk with paragraph-boundary snapping; drops tiny chunks."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        if end < len(text):
            newline = text.rfind("\n", start + size - overlap, end + overlap)
            if newline > start:
                end = newline
        chunks.append(text[start:end].strip())
        start = end - overlap if end < len(text) else len(text)
    return [c for c in chunks if len(c) > 50]


def chunk_document(text: str, chunk_size: int = 1500, overlap: int = 200) -> list[dict]:
    """Split a document by `Điều N`; sub-chunk over-long articles. Falls back to
    plain sliding-window chunking when fewer than 4 articles are found."""
    chunks: list[dict] = []
    articles = list(_ARTICLE_RE.finditer(text))
    if len(articles) > 3:
        for i, match in enumerate(articles):
            start = match.start()
            end = articles[i + 1].start() if i + 1 < len(articles) else len(text)
            article_text = text[start:end].strip()
            num_match = _ARTNUM_RE.match(article_text)
            num = num_match.group(1) if num_match else str(i + 1)
            title = match.group(1).strip()[:200]
            if len(article_text) > chunk_size * 2:
                subs = simple_chunk(article_text, chunk_size, overlap)
                for j, sub in enumerate(subs):
                    chunks.append({
                        "article": f"Điều {num}",
                        "clause": f"phần {j + 1}" if len(subs) > 1 else None,
                        "content": sub,
                        "title": title,
                    })
            else:
                chunks.append({"article": f"Điều {num}", "clause": None,
                               "content": article_text, "title": title})
    else:
        for i, sub in enumerate(simple_chunk(text, chunk_size, overlap)):
            chunks.append({"article": None, "clause": None,
                           "content": sub, "title": f"Phần {i + 1}"})
    return chunks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_law_ingest.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/_law_ingest.py tests/test_law_ingest.py
git commit -m "feat: _law_ingest chunking (simple_chunk, chunk_document) with tests"
```

---

## Task 5: `_law_ingest.py` — domain detection (enum-safe, review #2)

**Files:**
- Modify: `scripts/_law_ingest.py`
- Test: `tests/test_law_ingest.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_law_ingest.py`:

```python
from scripts._law_ingest import VALID_DOMAINS, detect_domains


def test_detect_domains_finds_labor():
    out = detect_domains("Bộ luật Lao động", "quy định về người lao động và tiền lương")
    assert "lao_dong" in out


def test_detect_domains_defaults_to_other():
    assert detect_domains("Tiêu đề trung tính", "nội dung không khớp") == ["other"]


def test_detect_domains_only_valid_enum_values():
    out = detect_domains("thuế thu nhập doanh nghiệp", "đất đai đầu tư bảo hiểm xã hội")
    assert set(out) <= VALID_DOMAINS


def test_valid_domains_has_twelve_labels():
    assert len(VALID_DOMAINS) == 12
    assert "hanh_chinh" not in VALID_DOMAINS  # the main.py bug must not leak in
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_law_ingest.py -v`
Expected: FAIL — `ImportError: cannot import name 'VALID_DOMAINS'`

- [ ] **Step 3: Add domain detection**

Append to `scripts/_law_ingest.py`:

```python
# The 12 legal_domain enum labels (database/init.sql). NOTE: `hanh_chinh` is
# intentionally absent — src/api/main.py:detect_domain emits it and it is NOT a
# legal_domain member, so we must never reuse that function here (review #2).
VALID_DOMAINS = frozenset({
    "lao_dong", "doanh_nghiep", "dan_su", "thuong_mai", "thue", "dat_dai",
    "dau_tu", "bhxh", "atvs_ld", "so_huu_tri_tue", "hinh_su", "other",
})

# Ported verbatim from the enum-safe scripts/load_law_data.py:detect_domains.
_DOMAIN_KEYWORDS = {
    "lao_dong": ["lao động", "người lao động", "người sử dụng lao động", "tiền lương", "hợp đồng lao động"],
    "doanh_nghiep": ["doanh nghiệp", "công ty", "thành lập doanh nghiệp", "cổ phần", "trách nhiệm hữu hạn"],
    "dan_su": ["dân sự", "quyền sở hữu", "thừa kế", "hợp đồng dân sự"],
    "thuong_mai": ["thương mại", "mua bán hàng hóa", "xuất nhập khẩu"],
    "thue": ["thuế", "thu nhập", "giá trị gia tăng", "thuế suất"],
    "dat_dai": ["đất đai", "quyền sử dụng đất", "thu hồi đất", "bất động sản"],
    "dau_tu": ["đầu tư", "vốn đầu tư", "nhà đầu tư", "dự án đầu tư"],
    "bhxh": ["bảo hiểm xã hội", "bảo hiểm y tế", "bảo hiểm thất nghiệp", "hưu trí"],
    "atvs_ld": ["an toàn", "vệ sinh lao động", "tai nạn lao động", "bệnh nghề nghiệp"],
    "so_huu_tri_tue": ["sở hữu trí tuệ", "bản quyền", "sáng chế", "nhãn hiệu"],
    "hinh_su": ["hình sự", "tội phạm", "hình phạt", "truy cứu"],
}


def detect_domains(title: str | None, content: str | None) -> list[str]:
    """Keyword-detect legal domains from title + first 5000 chars of content.
    Guarantees every returned value is a legal_domain enum member."""
    text = (str(title or "") + " " + str(content or "")[:5000]).lower()
    found = [d for d, kws in _DOMAIN_KEYWORDS.items() if any(k in text for k in kws)]
    result = found or ["other"]
    invalid = set(result) - VALID_DOMAINS
    assert not invalid, f"detect_domains produced non-enum values: {invalid}"
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_law_ingest.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/_law_ingest.py tests/test_law_ingest.py
git commit -m "feat: _law_ingest enum-safe detect_domains + VALID_DOMAINS (review #2)"
```

---

## Task 6: `_law_ingest.py` — enum maps, number normalize, date parse

**Files:**
- Modify: `scripts/_law_ingest.py`
- Test: `tests/test_law_ingest.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_law_ingest.py`:

```python
from datetime import date

from scripts._law_ingest import (
    map_law_type_legacy,
    map_law_type_vi,
    map_status_legacy,
    map_status_vi,
    normalize_doc_number,
    parse_date_vi,
)


def test_map_law_type_vi():
    assert map_law_type_vi("Quyết định") == "quyet_dinh"
    assert map_law_type_vi("Thông tư liên tịch") == "thong_tu"
    assert map_law_type_vi("Hiến pháp") == "hien_phap"
    assert map_law_type_vi("Chỉ thị") == "other"
    assert map_law_type_vi(None) == "other"


def test_map_law_type_legacy():
    assert map_law_type_legacy("Decision") == "quyet_dinh"
    assert map_law_type_legacy("Official Dispatch") == "cong_van"
    assert map_law_type_legacy("Circular") == "thong_tu"
    assert map_law_type_legacy("Kế hoạch") == "other"


def test_map_status_vi():
    assert map_status_vi("Còn hiệu lực") == "active"
    assert map_status_vi("Hết hiệu lực toàn bộ") == "expired"
    assert map_status_vi("Hết hiệu lực một phần") == "amended"
    assert map_status_vi("") == "active"


def test_map_status_legacy():
    assert map_status_legacy("In effect") == "active"
    assert map_status_legacy("Expired") == "expired"
    assert map_status_legacy("No longer applicable") == "expired"
    assert map_status_legacy("Unknown") == "active"


def test_normalize_doc_number():
    assert normalize_doc_number("  115/nq-hđbcqg  ") == "115/NQ-HĐBCQG"
    assert normalize_doc_number("01 / 2020 / TT") == "01 / 2020 / TT"
    assert normalize_doc_number(None) == ""


def test_parse_date_vi():
    assert parse_date_vi("15/03/2020") == date(2020, 3, 15)
    assert parse_date_vi("2020-03-15") == date(2020, 3, 15)
    assert parse_date_vi("garbage") is None
    assert parse_date_vi("") is None
    assert parse_date_vi("31/02/2020") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_law_ingest.py -v`
Expected: FAIL — `ImportError: cannot import name 'map_law_type_vi'`

- [ ] **Step 3: Add the maps and parsers**

Append to `scripts/_law_ingest.py`:

```python
_LAW_TYPE_VI = {
    "quyết định": "quyet_dinh",
    "nghị quyết": "nghi_quyet",
    "nghị quyết liên tịch": "nghi_quyet",
    "thông tư": "thong_tu",
    "thông tư liên tịch": "thong_tu",
    "thông tư liên bộ": "thong_tu",
    "nghị định": "nghi_dinh",
    "luật": "luat",
    "bộ luật": "bo_luat",
    "hiến pháp": "hien_phap",
    "công văn": "cong_van",
}

_LAW_TYPE_LEGACY = {
    "decision": "quyet_dinh",
    "official dispatch": "cong_van",
    "resolution": "nghi_quyet",
    "circular": "thong_tu",
    "joint circular": "thong_tu",
    "decree of government": "nghi_dinh",
    "law": "luat",
    "constitution": "hien_phap",
}

_STATUS_VI = {
    "còn hiệu lực": "active",
    "hết hiệu lực toàn bộ": "expired",
    "hết hiệu lực một phần": "amended",
    "chưa có hiệu lực": "pending",
    "không còn phù hợp": "expired",
}

_STATUS_LEGACY = {
    "in effect": "active",
    "expired": "expired",
    "no longer applicable": "expired",
}


def map_law_type_vi(value: str | None) -> str:
    return _LAW_TYPE_VI.get(str(value or "").strip().lower(), "other")


def map_law_type_legacy(value: str | None) -> str:
    return _LAW_TYPE_LEGACY.get(str(value or "").strip().lower(), "other")


def map_status_vi(value: str | None) -> str:
    return _STATUS_VI.get(str(value or "").strip().lower(), "active")


def map_status_legacy(value: str | None) -> str:
    return _STATUS_LEGACY.get(str(value or "").strip().lower(), "active")


def normalize_doc_number(value: str | None) -> str:
    """Dedup key: collapse internal whitespace, strip, uppercase."""
    return re.sub(r"\s+", " ", str(value or "").strip()).upper()


def parse_date_vi(value: str | None) -> date | None:
    """Parse DD/MM/YYYY or YYYY-MM-DD; return None on anything else/invalid."""
    s = str(value or "").strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_law_ingest.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/_law_ingest.py tests/test_law_ingest.py
git commit -m "feat: _law_ingest enum maps + normalize_doc_number + parse_date_vi"
```

---

## Task 7: Loader skeleton — env config + snapshot resolution

**Files:**
- Create: `scripts/load_offline_dataset.py`
- Test: `tests/test_load_offline_smoke.py`

- [ ] **Step 1: Write the failing test (snapshot resolution is pure-ish, uses tmp dirs)**

Create `tests/test_load_offline_smoke.py`:

```python
import os

import pytest

from scripts.load_offline_dataset import build_db_config, resolve_snapshot


def test_resolve_snapshot_picks_newest(tmp_path):
    base = tmp_path / "data" / "datasets-legal-docs" / "snapshots"
    old = base / "aaa"
    new = base / "bbb"
    for d in (old, new):
        (d / "data").mkdir(parents=True)
        (d / "data" / "metadata.parquet").write_bytes(b"x")
    os.utime(new, (10**9 + 100, 10**9 + 100))  # make `new` newer
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_load_offline_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.load_offline_dataset'`

- [ ] **Step 3: Create the loader skeleton**

Create `scripts/load_offline_dataset.py`:

```python
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
```

- [ ] **Step 4: Ensure `scripts/` is importable as a package**

Confirm `scripts/__init__.py` exists (it does in this repo). If missing, create an empty one:

Run: `test -f scripts/__init__.py && echo OK || touch scripts/__init__.py`
Expected: `OK`

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_load_offline_smoke.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add scripts/load_offline_dataset.py tests/test_load_offline_smoke.py
git commit -m "feat: offline loader skeleton (env config + snapshot resolution)"
```

---

## Task 8: Phase 0 — schema reconciliation + truncate + index management

**Files:**
- Modify: `scripts/load_offline_dataset.py`

- [ ] **Step 1: Add schema-reconciliation, truncate, and index helpers**

Insert these functions in `scripts/load_offline_dataset.py` above `main()`:

```python
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


def truncate_and_drop_indexes(conn) -> None:
    with conn.cursor() as cur:
        for name in TSV_INDEXES:
            cur.execute(f"DROP INDEX IF EXISTS {name}")
        cur.execute("TRUNCATE law_chunks, law_relations, law_documents")
    conn.commit()
    print("[load] truncated law_* and dropped tsv GIN indexes")


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
```

- [ ] **Step 2: Wire Phase 0 into `main()` with crash-safe index recreation**

Replace the body of `main()` after the two `print(...)` lines (remove the `raise SystemExit(...)` placeholder) with:

```python
    conn = psycopg2.connect(**cfg)
    try:
        reconcile_schema(conn)
        if args.truncate:
            truncate_and_drop_indexes(conn)
        else:
            assert_empty_or_die(conn)
        # Phases 1-3 wired in Tasks 9-11:
        # id_map, seen = load_current(conn, snap, args.limit)
        # if not args.skip_legacy: load_legacy(conn, snap, seen, args.limit)
        # if not args.skip_relations: load_relationships(conn, snap, id_map)
    finally:
        # Crash-safe (review #7): always rebuild the GIN indexes we dropped.
        recreate_indexes(conn)
        conn.close()
    return 0
```

- [ ] **Step 3: Smoke-check imports (no DB needed)**

Run: `python -c "import scripts.load_offline_dataset as m; print('reconcile_schema' in dir(m), 'recreate_indexes' in dir(m))"`
Expected: `True True`

- [ ] **Step 4: Run unit tests to confirm no regressions**

Run: `pytest tests/test_load_offline_smoke.py tests/test_law_ingest.py -v`
Expected: PASS (all prior tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/load_offline_dataset.py
git commit -m "feat: loader Phase 0 — schema reconcile, truncate, crash-safe indexes"
```

---

## Task 9: Phase 1 — load current config (HTML, rich metadata)

**Files:**
- Modify: `scripts/load_offline_dataset.py`

- [ ] **Step 1: Add batched insert helpers**

Insert above `main()` in `scripts/load_offline_dataset.py`:

```python
_DOC_COLS = ("id", "title", "law_number", "law_type", "issuer", "signer",
             "issued_date", "effective_date", "expiry_date", "status",
             "domains", "full_text", "source_site", "source_url",
             "article_count", "word_count")

# Trailing two %s (title, full_text) feed the tsv expression (review #6 coalesce).
_DOC_TEMPLATE = ("(" + ",".join(["%s"] * len(_DOC_COLS)) +
                 ", to_tsvector('simple', coalesce(%s,'') || ' ' || coalesce(%s,'')))")

_CHUNK_COLS = ("law_id", "article", "clause", "title", "content", "domains")
_CHUNK_TEMPLATE = ("(" + ",".join(["%s"] * len(_CHUNK_COLS)) +
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
```

- [ ] **Step 2: Add `load_current()`**

Insert above `main()`:

```python
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
        article_count = text.count("Điều ")
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
```

- [ ] **Step 3: Wire `load_current` into `main()`**

In `main()`, replace the Phase 1 comment with:

```python
        id_map, seen = load_current(conn, snap, args.limit)
```

(Leave the Phases 2-3 comments for now.)

- [ ] **Step 4: Smoke-check import**

Run: `python -c "import scripts.load_offline_dataset as m; print('load_current' in dir(m))"`
Expected: `True`

- [ ] **Step 5: Commit**

```bash
git add scripts/load_offline_dataset.py
git commit -m "feat: loader Phase 1 — current config (group, clean, chunk, tsv, id_map)"
```

---

## Task 10: Phase 2 — load legacy config (plaintext, dedup)

**Files:**
- Modify: `scripts/load_offline_dataset.py`

- [ ] **Step 1: Add `load_legacy()`**

Insert above `main()`:

```python
def _group_legacy_content(content_path: str) -> dict[int, str]:
    table = pq.read_table(content_path, columns=["id", "content"])
    ids = table.column("id").to_pylist()
    texts = table.column("content").to_pylist()
    grouped: dict[int, list[str]] = {}
    for rid, txt in zip(ids, texts):
        if rid is None:
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
            text.count("Điều "), len(text.split()), title, text,
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
```

- [ ] **Step 2: Wire into `main()`**

In `main()`, replace the Phase 2 comment with:

```python
        if not args.skip_legacy:
            load_legacy(conn, snap, seen, args.limit)
```

- [ ] **Step 3: Smoke-check import**

Run: `python -c "import scripts.load_offline_dataset as m; print('load_legacy' in dir(m))"`
Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add scripts/load_offline_dataset.py
git commit -m "feat: loader Phase 2 — legacy config with number-dedup"
```

---

## Task 11: Phase 3 — relationships + Phase 4 verification

**Files:**
- Modify: `scripts/load_offline_dataset.py`

- [ ] **Step 1: Add `load_relationships()` and `verify()`**

Insert above `main()`:

```python
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
        assert tsv_hits > 0, "tsv populated but no keyword match — check to_tsvector"

        # End-to-end via the live search_law() (deployed in Phase 0).
        cur.execute("SELECT count(*) FROM search_law(%s, NULL, %s)",
                    ("hợp đồng lao động", 10))
        sl_hits = cur.fetchone()[0]
        print(f"[verify] search_law('hợp đồng lao động') rows: {sl_hits}")
        assert sl_hits > 0, "search_law returned nothing"

        if not skip_relations and limit is None:
            assert rels > 0, "expected relationships on a full run"
        else:
            print("[verify] relationship-count assertion skipped (--limit/--skip-relations)")
    print("[verify] OK")
```

- [ ] **Step 2: Wire Phase 3 + verify into `main()`**

In `main()`, replace the Phase 3 comment with the relationship load **plus** the success-path finalize (recreate indexes + verify). `verify()` must run on the success path, **not** in `finally` — its asserts would otherwise mask the real exception during a failed load. The `finally` block keeps only the crash-safe `recreate_indexes(conn)` as an idempotent safety net (`CREATE INDEX IF NOT EXISTS` makes the success-path call + the finally call harmless if both run).

```python
        if not args.skip_relations:
            load_relationships(conn, snap, id_map)
        recreate_indexes(conn)          # Phase 4 step 1 (success path)
        verify(conn, args.limit, args.skip_relations)  # Phase 4 step 3
```

Leave the `finally` block exactly as written in Task 8 (it still calls `recreate_indexes(conn)` then `conn.close()`). On success, indexes are recreated in the try block and the finally call is a no-op via `IF NOT EXISTS`; on crash, the finally call rebuilds them.

- [ ] **Step 3: Smoke-check import**

Run: `python -c "import scripts.load_offline_dataset as m; print('load_relationships' in dir(m), 'verify' in dir(m))"`
Expected: `True True`

- [ ] **Step 4: Commit**

```bash
git add scripts/load_offline_dataset.py
git commit -m "feat: loader Phase 3 relationships + Phase 4 verify (tsv + search_law)"
```

---

## Task 12: DB-gated integration smoke test

Runs the loader end-to-end on a small subset against a live Docker DB. Skipped automatically when no DB is reachable, so it never breaks CI.

**Files:**
- Modify: `tests/test_load_offline_smoke.py`

- [ ] **Step 1: Add the gated integration test**

Append to `tests/test_load_offline_smoke.py`:

```python
import psycopg2

from scripts.load_offline_dataset import build_db_config, main, resolve_snapshot


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
    rc = main(["--truncate", "--limit", "50", "--skip-relations"])
    assert rc == 0
    conn = psycopg2.connect(**build_db_config())
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM law_documents")
        assert cur.fetchone()[0] > 0
        cur.execute("SELECT count(*) FROM law_chunks WHERE tsv IS NOT NULL")
        assert cur.fetchone()[0] > 0
        cur.execute("SELECT count(*) FROM search_law(%s, NULL, %s)", ("lao động", 5))
        assert cur.fetchone()[0] > 0
    conn.close()
```

- [ ] **Step 2: Run the suite (no DB → test skips, not fails)**

Run: `pytest tests/test_load_offline_smoke.py -v`
Expected: 3 pass + 1 skipped (`needs live Docker DB + dataset snapshot`), OR all 4 pass if a Docker DB + dataset are present.

- [ ] **Step 3: (If Docker available) run the real end-to-end**

Run:
```bash
docker compose up -d db
export SUPABASE_DB_HOST=localhost SUPABASE_DB_PORT=5432 \
       DB_NAME=legalai DB_USER=legalai SUPABASE_DB_PASSWORD=legalai2026 DB_SSL_MODE=disable
pytest tests/test_load_offline_smoke.py::test_loader_end_to_end_subset -v
```
Expected: PASS — loader reconciles schema, loads 50 docs, `search_law('lao động')` returns rows.

- [ ] **Step 4: Commit**

```bash
git add tests/test_load_offline_smoke.py
git commit -m "test: DB-gated end-to-end smoke for offline loader"
```

---

## Task 13: README — offline-load section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the database-setup load step**

In `README.md`, find the "Database Setup" block that currently reads:

```bash
# Load Vietnamese law data (optional, ~40K documents)
python scripts/load_law_data.py
python scripts/index_chunks.py
```

Replace it with:

```bash
# Load Vietnamese law data from the bundled offline dataset (primary path)
# Runs on the HOST against the docker-compose Postgres:
export SUPABASE_DB_HOST=localhost
export SUPABASE_DB_PORT=${DB_PORT:-5432}
export DB_NAME=${POSTGRES_DB:-legalai}
export DB_USER=${POSTGRES_USER:-legalai}
export SUPABASE_DB_PASSWORD=${POSTGRES_PASSWORD:-legalai2026}
export DB_SSL_MODE=disable
python scripts/load_offline_dataset.py --truncate
```

- [ ] **Step 2: Add an explanatory note under that block**

Immediately after, add:

```markdown
> The loader auto-reconciles the schema it needs (`pg_trgm`, `domains legal_domain[]`,
> and the `search_law()` function from `scripts/migration_search_v5_fixed.sql`), then
> loads the bundled HuggingFace dataset (`data/datasets-legal-docs/`) — both the
> `current` (HTML, rich metadata) and `legacy` (broader coverage) configs, merged and
> deduplicated. Use `--limit N --skip-relations` to validate on a subset first, or
> `--skip-legacy` for a faster current-only corpus. The CrawlKit crawler remains
> available for ad-hoc top-ups but is no longer required.
```

- [ ] **Step 3: Verify the edit**

Run: `grep -n "load_offline_dataset" README.md`
Expected: at least one match in the Database Setup section.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README offline-dataset load as primary populate path"
```

---

## Final Verification

- [ ] **Run the full unit suite**

Run: `pytest tests/test_law_ingest.py tests/test_load_offline_smoke.py -v`
Expected: all unit tests PASS; the DB-gated test PASSes or SKIPs.

- [ ] **Confirm CI import smoke still green (loader is a script, not imported by the app)**

Run: `python -c "from src.rag.search import *; from src.agents.legal_agent import *; print('app imports OK')"`
Expected: `app imports OK`

- [ ] **(If Docker available) full load**

Run the README command without `--limit`. Expected: completes (long-running), `[verify] OK`, `search_law()` returns rows, `law_relations` populated.

---

## Notes for the implementer

- **`scripts._law_ingest` import path:** tests and the loader import as `scripts._law_ingest` / `scripts.load_offline_dataset`, so run `pytest` from the repo root (where `scripts/__init__.py` exists). This matches the existing `scripts/` package layout.
- **`_legal_domain` udt check:** Postgres reports an array-of-enum column's `udt_name` as `_legal_domain` (leading underscore). The `reconcile_schema` type guard depends on this exact value — do not change it to `legal_domain[]`.
- **tsv via `execute_values` template:** the doc/chunk `template` strings append `to_tsvector('simple', …)` columns, so each row tuple repeats `title`/`full_text` (docs) or `content` (chunks) at the end as the tsv operands. Keep the tuple field order in lock-step with `_DOC_COLS` + 2 / `_CHUNK_COLS` + 1.
- **Memory:** `_group_content_by_id` reads the 412M current content parquet in one pass. If the host is memory-constrained, switch to `pq.ParquetFile(path).iter_batches(...)` per spec §10; start with the single-read version.
- **Do not reuse `src/api/main.py:detect_domain`** — it emits `hanh_chinh`, which is not a `legal_domain` member and would fail the `domains::legal_domain[]` insert (review #2). Always use `scripts._law_ingest.detect_domains`.
