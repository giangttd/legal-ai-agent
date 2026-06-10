# Offline Legal Dataset Loader — Design

**Date:** 2026-06-10
**Branch:** `feat/offline-legal-dataset`
**Status:** Approved (design), pending implementation plan

## 1. Goal

Populate the law database from the offline HuggingFace dataset (`th1nhng0/vietnamese-legal-documents`) bundled under `data/datasets-legal-docs/` instead of relying on the CrawlKit web crawler. The crawler stays available for ad-hoc top-ups but is no longer required to have a usable law corpus.

Non-goals: vector embeddings (keyword/tsvector search only), Supabase cloud load, removing the crawler, building any new UI.

## 2. Decisions (locked)

| Decision | Choice |
|---|---|
| Crawler | Keep, make optional. No code change — already gated by `CRAWLKIT_API_KEY`. Offline load becomes the primary populate path. |
| Dataset config | **Both** `current` + `legacy`, merged with dedup. |
| Search mode | **Keyword-only.** Populate `tsv`; leave `embedding` NULL. |
| Target DB | **Local Docker Postgres** (`docker-compose` pgvector), full load. |
| Relationships | **Load** into `law_relations` with int→UUID remapping. |
| Architecture | **Single streaming Python loader** (Approach A). |

## 3. Dataset facts (verified against the actual parquet files, not the README)

Snapshot path: `data/datasets-legal-docs/snapshots/<rev>/` (current rev `0a39ad7eae8e6c188cb225c4b1443c3b346461d8`; auto-detect via `refs/main`, override with `--dataset-dir`).

### current config (`<snap>/data/`)
- `metadata.parquet` — 153,420 rows. `id int64`, plus Vietnamese fields: `title`, `so_ky_hieu`, `ngay_ban_hanh`, `loai_van_ban`, `ngay_co_hieu_luc`, `ngay_het_hieu_luc`, `co_quan_ban_hanh`, `nguoi_ky`, `tinh_trang_hieu_luc`, `nganh`, `linh_vuc`, … (`thong_tin_ap_dung` is `double`, mostly null — ignore).
- `content.parquet` — 178,665 rows. `id large_string` (numeric string e.g. `"4260"`), `content_html large_string` (raw HTML).
- `relationships.parquet` — 897,890 rows. `doc_id int64`, `other_doc_id int64`, `relationship large_string`.

### legacy config (`<snap>/legacy/`)
- `metadata.parquet` — 518,601 rows. `id int64`, `document_number`, `title`, `legal_type`, `legal_sectors`, `issuing_authority`, `issuance_date`, `effect_date`, `effectless_date`, `effect_status`, `signers`.
- `content.parquet` — 518,235 rows. `id int64`, `content` (plain text, no HTML).

### Key verified properties (drove the design)
1. **`content.id` is a string** of the int → cast to int to join `metadata.id` and to key the relationship map.
2. **`content.parquet` has duplicate ids** — 178,665 rows / **149,051 unique ids**. ~29.6K docs are split across multiple rows → must group by id and concatenate `content_html` before cleaning/chunking.
3. **Coverage:** 146,857 docs have both content+metadata; 6,563 metadata-only (skip — unsearchable); 2,194 content-only (insert with stub metadata).
4. **`so_ky_hieu` 0% missing**, legacy **`document_number` 0% missing** → reliable dedup key + `law_number` (no fallback needed except content-only docs).
5. **`co_quan_ban_hanh` 1.2% null** → `issuer` fallback `"Chưa xác định"`.
6. **relationships:** 93.6% (840,100) have both endpoints in current metadata → loadable; skip 6.4% dangling.
7. `idx_law_documents_law_number` is a **non-unique** index → partial dedup won't fail inserts (dedup is for quality, not correctness).
8. `embedding vector` is dimensionless + ivfflat index accepts NULL → keyword-only load is valid.

## 4. Target schema (existing, unchanged)

`law_documents` (`id UUID`, `title`, `law_number NOT NULL`, `law_type law_type NOT NULL`, `issuer NOT NULL`, `signer`, `issued_date`, `effective_date`, `expiry_date`, `status law_status DEFAULT 'active'`, `domains TEXT[] NOT NULL`, `full_text`, `source_url`, `source_site`, `article_count`, `word_count`, `tsv TSVECTOR`, …).

`law_chunks` (`id UUID`, `law_id UUID`, `chapter`, `section`, `article`, `clause`, `point`, `title`, `content NOT NULL`, `parent_context`, `embedding vector`, `domains TEXT[]`, `keywords TEXT[]`, `tsv TSVECTOR`).

`law_relations` (`id UUID`, `source_law_id UUID`, `source_article`, `target_law_id UUID`, `target_article`, `relation_type`).

Enums: `law_type = {hien_phap, bo_luat, luat, nghi_dinh, thong_tu, quyet_dinh, nghi_quyet, cong_van, other}`; `law_status = {active, expired, amended, repealed, pending}`; `legal_domain = {lao_dong, doanh_nghiep, dan_su, thuong_mai, thue, dat_dai, dau_tu, bhxh, atvs_ld, so_huu_tri_tue, hinh_su, other}`.

**Critical correctness point:** `tsv` has **no trigger and no generated column** anywhere (verified in `database/init.sql` + all `scripts/migration_search_*.sql`). The live search function `search_law()` filters `lc.tsv @@ to_tsquery('simple', …)`. Therefore the loader **must** populate `tsv` with `to_tsvector('simple', …)` — matching config `'simple'`. The old `load_law_data.py` omitted this; that is the bug this loader fixes. (The ILIKE-on-`content` phase 1 of `multi_query_search` works without tsv, but phase 2 recall depends on it.)

## 5. Components

### 5.1 `scripts/_law_ingest.py` (shared helpers)
Extracted so both the new loader and the legacy `load_law_data.py` can share them. Pure functions, unit-testable:
- `clean_html(html: str) -> str` — BeautifulSoup (`lxml`), drop `<script>/<style>`, `get_text` with newline separators, collapse whitespace.
- `chunk_document(text: str) -> list[dict]` — split by `Điều N` (reuse existing logic), sub-chunk long articles via `simple_chunk`, returns `{article, clause, title, content}`.
- `simple_chunk(text, size=1500, overlap=200) -> list[str]`.
- `detect_domains(title, content) -> list[str]` — reuse existing keyword logic; default `['other']`. Returns only `legal_domain`-valid values.
- `map_law_type_vi(loai_van_ban) -> str`, `map_law_type_legacy(legal_type) -> str`.
- `map_status_vi(tinh_trang) -> str`, `map_status_legacy(effect_status) -> str`.
- `normalize_doc_number(s) -> str` — upper, strip, collapse internal whitespace (dedup key).
- `parse_date_vi(s) -> date | None` — handle `DD/MM/YYYY` and `YYYY-MM-DD`; return None on failure.

### 5.2 `scripts/load_offline_dataset.py` (CLI orchestrator)
Flags:
- `--dataset-dir PATH` — override snapshot dir (default: auto-detect newest under `data/datasets-legal-docs/snapshots/`).
- `--truncate` — `TRUNCATE law_chunks, law_relations, law_documents CASCADE` before load. **Required** to overwrite; without it, abort if tables non-empty (prevents accidental wipe / duplicate stacking).
- `--limit N` — cap docs per config (pipeline validation on a subset).
- `--skip-relations` — skip the relationships pass.
- `--skip-legacy` — load only current config.

DB connection via existing `DB_*` / `SUPABASE_DB_*` env (same `DB_CONFIG` shape as `main.py`). Run **on host** against `localhost:5432`.

## 6. Load pipeline

**Phase 0 — prep**
1. Resolve snapshot dir.
2. If `--truncate`: drop GIN indexes `idx_law_documents_tsv`, `idx_law_chunks_tsv` (recreate at end for bulk-insert speed), then TRUNCATE. Else abort if non-empty.

**Phase 1 — current config**
1. Load `metadata.parquet` fully into `meta_by_id: dict[int, row]` (~153K, small).
2. Stream `content.parquet`; **group rows by `int(id)`**, concatenating `content_html` (ordered by row appearance) so multi-row docs become one. (Implementation: since duplicates are ~16%, accumulate into `dict[int, list[str]]` in a first streaming pass, or sort by id — full content parquet is 412M, fits a single read; concat in memory.)
3. For each unique doc id:
   - `clean_html` → plaintext. Skip if cleaned text < 100 chars.
   - Enrich from `meta_by_id` if present (146,857), else stub (`law_number="VBPL-{id}"`, `issuer="Chưa xác định"`, `law_type=other`, `status=active`, `domains=['other']`).
   - Build `law_documents` row: `law_number = so_ky_hieu`, `law_type = map_law_type_vi(loai_van_ban)`, `issuer = co_quan_ban_hanh or "Chưa xác định"`, `signer = nguoi_ky`, dates via `parse_date_vi`, `status = map_status_vi`, `domains = detect_domains`, `full_text = plaintext`, `source_site = "huggingface/th1nhng0"`, `source_url = "https://vbpl.vn/.../{id}"` (or store id for traceability), `word_count`, `article_count`.
   - `INSERT … RETURNING id` → record `id_map[int_id] = uuid`. tsv = `to_tsvector('simple', title || ' ' || full_text)`.
   - `chunk_document` → batch-insert `law_chunks` (`execute_values`), each with `domains`, tsv = `to_tsvector('simple', content)`.
4. Record `seen_numbers = {normalize_doc_number(so_ky_hieu)}` for dedup.

**Phase 2 — legacy config** (skipped if `--skip-legacy`)
1. Load legacy `metadata.parquet` into `dict[int, row]`.
2. Stream legacy `content.parquet`; group by id (concat if duplicates).
3. For each doc: skip if `normalize_doc_number(document_number)` already in `seen_numbers` (or title-normalized fallback if number empty — but legacy number is 0% missing). Skip if content < 100 chars.
4. Insert `law_documents` (legacy maps: `map_law_type_legacy`, `map_status_legacy`, `issuer = issuing_authority`, `signer` from `signers`) + chunks, same tsv treatment. Legacy ids are a **separate id space** → not added to `id_map` (relationships are current-only).

**Phase 3 — relationships** (skipped if `--skip-relations`)
1. Stream `relationships.parquet`.
2. For each edge: `s = id_map.get(doc_id)`, `t = id_map.get(other_doc_id)`. Insert `(source_law_id=s, target_law_id=t, relation_type=relationship)` only if both resolve; else skip. Batched `execute_values`.

**Phase 4 — finalize**
1. Recreate GIN tsv indexes (if dropped).
2. `ANALYZE law_documents; ANALYZE law_chunks; ANALYZE law_relations;`.
3. Print counts + run 2–3 sample queries through `search_law()` (e.g. "hợp đồng lao động", "thuế giá trị gia tăng") and assert non-empty results → verifies tsv populated and query path works.

## 7. Performance

- Full merged load is large (~hundreds of thousands of docs, multi-million chunks; legacy content parquet ~3.3GB uncompressed). Expect a long run (likely hours) and tens of GB of DB + GIN index.
- Mitigations: drop GIN tsv indexes during load + recreate after; `execute_values` with `page_size≈1000`; commit every N docs; stream content in row-group batches where memory pressure appears.
- `--limit` validates the full pipeline end-to-end on a subset before committing to the full run.

## 8. Dependencies & hygiene

- `requirements.txt`: add `pyarrow`, `beautifulsoup4`, `lxml`.
- `.gitignore`: add `data/datasets-legal-docs/` and `data/*.json` snapshots (400M+ — keep out of git).
- `.dockerignore`: add `data/datasets-legal-docs/` (Dockerfile does `COPY data/ ./data/` — without this the image bloats by multiple GB). Loader runs on host, not in the app image, so the dataset is never needed inside the container.
- README: add an "Offline dataset load" section documenting `python scripts/load_offline_dataset.py --truncate` as the primary populate path; note crawler is now optional.

## 9. Testing

- Unit tests (`pytest`) for `_law_ingest.py`: `clean_html` (strips tags, keeps text), `chunk_document` (splits by Điều, sub-chunks long articles), the four enum maps (cover every observed input value + unknown→`other`/`active`), `normalize_doc_number`, `parse_date_vi` (both date formats + garbage).
- Integration smoke (optional, gated on a DB): run loader with `--limit 50 --truncate` against a test DB, assert `law_documents`/`law_chunks` counts > 0, tsv non-null, and a `search_law()` query returns rows.
- CI already imports `src.rag.search` + `src.agents.legal_agent`; no change needed (loader is a script, not imported by the app).

## 10. Risks / open notes

- **Memory:** grouping current content in memory (412M parquet → larger uncompressed). If pressure: two-pass (first pass id→row-offsets, second pass per-id). Start simple (single read), escalate only if needed.
- **Run time:** full legacy load is the heavy part; `--skip-legacy` gives a fast current-only corpus (149K docs) if needed.
- **Dedup fidelity:** same law may have slightly different number formatting across crawls → `normalize_doc_number` reduces but won't eliminate all dupes; acceptable (non-unique index).
- **Date parsing:** Vietnamese date fields are free-form strings; failures → NULL date (not fatal).
