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
| Schema reconciliation | **In scope (expanded per review #1).** Align the Docker schema to the search contract: `domains` columns → `legal_domain[]`, add `pg_trgm`, deploy the canonical `search_law()`. Without this, keyword search does not work on a fresh Docker DB regardless of the loader. |

## 2a. Review revisions (x-review 2026-06-10, REQUEST_CHANGES → addressed)

This spec was revised after a cross-model review (Claude opus + GPT-5.5 + independent). Findings folded in:

- **#1 HIGH — schema divergence.** Docker `init.sql` declares `domains TEXT[]`, has no `pg_trgm`, and does **not** define `search_law()`; the live `search_law()` and `main.py` require `legal_domain[]` + `pg_trgm`. `run_migration.py` only applies `migration_auth.sql`. → §4a + §6 Phase 0 now reconcile the schema (decision 1b: fix end-to-end).
- **#2 HIGH — `detect_domains` ambiguity.** Two functions exist; `main.py:detect_domain` emits `hanh_chinh` (not a `legal_domain` member). → §5.1 pins the enum-safe `load_law_data.py:detect_domains` + hard whitelist assertion.
- **#3 HIGH — host→Docker DB connection.** `main.py` `DB_CONFIG` defaults (`localhost/postgres/postgres`, `sslmode=require`) don't match docker-compose (`legalai/…`, `sslmode=disable`). → §5.3 adds the exact host env block.
- **#4 MEDIUM — content-only stub missing `title` (NOT NULL).** → §6 Phase 1 stub now sets a `title` fallback.
- **#5 MEDIUM — `--limit` invalidates relationship validation.** → §7/§9 note Phase 3 resolves ~0 edges under `--limit`; validation pairs `--limit` with `--skip-relations`.
- **#6 LOW — tsv NULL-poison.** → `coalesce(…, '')` on all tsv operands.
- **#7 LOW — index drop/recreate not crash-safe.** → `DROP INDEX IF EXISTS` + recreate in a `finally` path.
- **#8 LOW — content-fragment concat order.** → dedup identical fragments; treat single-read row order as authoritative; documented.

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

## 4. Target schema (reconciled — see §4a)

After reconciliation the loader targets:

`law_documents` (`id UUID`, `title NOT NULL`, `law_number NOT NULL`, `law_type law_type NOT NULL`, `issuer NOT NULL`, `signer`, `issued_date`, `effective_date`, `expiry_date`, `status law_status DEFAULT 'active'`, `domains legal_domain[] NOT NULL`, `full_text`, `source_url`, `source_site`, `article_count`, `word_count`, `tsv TSVECTOR`, …).

`law_chunks` (`id UUID`, `law_id UUID`, `chapter`, `section`, `article`, `clause`, `point`, `title`, `content NOT NULL`, `parent_context`, `embedding vector`, `domains legal_domain[]`, `keywords TEXT[]`, `tsv TSVECTOR`). Only `id`, `law_id`, `content` are NOT NULL — the chunker leaving `chapter/section/point/parent_context` NULL is valid.

`law_relations` (`id UUID`, `source_law_id UUID`, `source_article`, `target_law_id UUID`, `target_article`, `relation_type`).

Enums: `law_type = {hien_phap, bo_luat, luat, nghi_dinh, thong_tu, quyet_dinh, nghi_quyet, cong_van, other}`; `law_status = {active, expired, amended, repealed, pending}`; `legal_domain = {lao_dong, doanh_nghiep, dan_su, thuong_mai, thue, dat_dai, dau_tu, bhxh, atvs_ld, so_huu_tri_tue, hinh_su, other}`.

**Critical correctness point:** `tsv` has **no trigger and no generated column** anywhere (verified in `database/init.sql` + all `scripts/migration_search_*.sql`). The live search function `search_law()` filters `lc.tsv @@ to_tsquery('simple', …)`. Therefore the loader **must** populate `tsv` with `to_tsvector('simple', …)` — matching config `'simple'`. The old `load_law_data.py` omitted this; that is the bug this loader fixes. (The ILIKE-on-`content` phase 1 of `multi_query_search` works without tsv, but phase 2 recall depends on it.)

## 4a. Schema reconciliation (review finding #1 — scope expansion, decision 1b)

The Docker DB and the production search contract **diverge**, and a fresh Docker DB cannot run keyword search at all:

| Concern | `database/init.sql` (Docker, mounted by `docker-compose.yml:15`) | What `search_law()` + `src/api/main.py` require |
|---|---|---|
| `domains` columns | `TEXT[]` | `legal_domain[]` — `search_law` does `lc.domains && filter_domains` (`filter_domains legal_domain[]`) and `RETURNS … domains legal_domain[]`; `main.py:717` casts `%s::legal_domain[]`. `text[] && legal_domain[]` errors at query time. |
| `search_law()` function | **absent** (not in `init.sql`; only in `scripts/migration_search_*.sql`) | called as `SELECT * FROM search_law(%s, %s::legal_domain[], %s)` (`main.py:717,722`) |
| `pg_trgm` extension | **absent** (only `uuid-ossp`, `vector`) | `idx_law_chunks_content_trgm … USING gin(content gin_trgm_ops)` needs it (`migration_search_v5_fixed.sql:147`) |
| `run_migration.py` | applies **only** `migration_auth.sql` — never the schema/search migrations | — |

**Resolution (in scope for this work):**

1. **Root fix `database/init.sql`** (correct for fresh DBs): change both `domains TEXT[]` → `domains legal_domain[]` (lines 283, 302) and add `CREATE EXTENSION IF NOT EXISTS pg_trgm;` (alongside lines 9–10).
2. **Canonical search migration:** adopt `scripts/migration_search_v5_fixed.sql` as the single source of `search_law()` (latest; signature matches `main.py`; idempotent `CREATE OR REPLACE FUNCTION` + `CREATE INDEX IF NOT EXISTS`). The loader applies it in Phase 0 (see §6).
3. **Idempotent prep for existing/truncated DBs** (loader Phase 0, runs after `--truncate` so tables are empty → `ALTER TYPE` is trivial):
   - `CREATE EXTENSION IF NOT EXISTS pg_trgm;`
   - For each domains column not already `legal_domain[]`: `ALTER TABLE … ALTER COLUMN domains TYPE legal_domain[] USING domains::legal_domain[];` (guarded by an `information_schema` type check; safe on empty tables).
   - Apply `migration_search_v5_fixed.sql`.

Because `domains` is now `legal_domain[]`, writing any non-enum value is a **hard insert failure** (not silent corruption) — making the enum-safe detector in §5.1 + the whitelist assertion mandatory, not advisory.

> Pre-existing, **out of scope** to fix here: `main.py:detect_domain()` emits `hanh_chinh` (not a `legal_domain` member), so administrative-law *queries* 500 on the `::legal_domain[]` cast. The loader must not inherit this (§5.1); fixing the query-side detector is a separate change.

## 5. Components

### 5.1 `scripts/_law_ingest.py` (shared helpers)
Extracted so both the new loader and the legacy `load_law_data.py` can share them. Pure functions, unit-testable:
- `clean_html(html: str) -> str` — BeautifulSoup (`lxml`), drop `<script>/<style>`, `get_text` with newline separators, collapse whitespace.
- `chunk_document(text: str) -> list[dict]` — split by `Điều N` (reuse existing logic), sub-chunk long articles via `simple_chunk`, returns `{article, clause, title, content}`.
- `simple_chunk(text, size=1500, overlap=200) -> list[str]`.
- `detect_domains(title, content) -> list[str]` — **port the enum-safe `scripts/load_law_data.py:detect_domains`** (keys: `lao_dong, doanh_nghiep, dan_su, thuong_mai, thue, dat_dai, dau_tu, bhxh, atvs_ld, so_huu_tri_tue, hinh_su`, default `['other']`). **MUST NOT** use `src/api/main.py:detect_domain` — it emits `hanh_chinh`, which is not a `legal_domain` member (review #2). Add `VALID_DOMAINS = frozenset(<12 legal_domain labels>)` and a hard assertion `assert set(result) <= VALID_DOMAINS` before any insert — since `domains` is `legal_domain[]` (§4a), an invalid value is a hard insert failure.
- `map_law_type_vi(loai_van_ban) -> str`, `map_law_type_legacy(legal_type) -> str`.
- `map_status_vi(tinh_trang) -> str`, `map_status_legacy(effect_status) -> str`.
- `normalize_doc_number(s) -> str` — upper, strip, collapse internal whitespace (dedup key).
- `parse_date_vi(s) -> date | None` — handle `DD/MM/YYYY` and `YYYY-MM-DD`; return None on failure.

### 5.2 `scripts/load_offline_dataset.py` (CLI orchestrator)
Flags:
- `--dataset-dir PATH` — override snapshot dir (default: auto-detect newest under `data/datasets-legal-docs/snapshots/`).
- `--truncate` — `TRUNCATE law_chunks, law_relations, law_documents` before load (all three listed explicitly; no `CASCADE` needed — `init.sql` declares no FKs into these tables). **Required** to overwrite; without it, abort if tables non-empty (prevents accidental wipe / duplicate stacking).
- `--limit N` — cap docs per config (pipeline validation on a subset).
- `--skip-relations` — skip the relationships pass.
- `--skip-legacy` — load only current config.

DB connection via the same `DB_CONFIG` env-var names as `main.py` — see §5.3 (the defaults do **not** match docker-compose, so the env block is required).

### 5.3 Host → Docker DB connection (review finding #3)

`main.py:DB_CONFIG` reads `SUPABASE_DB_HOST` (default `localhost`), `SUPABASE_DB_PORT` (`5432`), `DB_NAME` (`postgres`), `DB_USER` (`postgres`), `SUPABASE_DB_PASSWORD` (empty), `DB_SSL_MODE` (`require`). docker-compose provisions Postgres as `legalai/legalai2026/legalai` and the DB-side translation (`SUPABASE_DB_HOST=db`, `DB_SSL_MODE=disable`) is injected **only into the app container**, not the host. The `.env.example` exposes only `POSTGRES_*`. So the documented host command needs an explicit env block, or it connects to the wrong/absent DB or fails on `sslmode=require`:

```bash
# Host run against the docker-compose Postgres (ports published on localhost)
export SUPABASE_DB_HOST=localhost
export SUPABASE_DB_PORT=${DB_PORT:-5432}
export DB_NAME=${POSTGRES_DB:-legalai}
export DB_USER=${POSTGRES_USER:-legalai}
export SUPABASE_DB_PASSWORD=${POSTGRES_PASSWORD:-legalai2026}
export DB_SSL_MODE=disable
python scripts/load_offline_dataset.py --truncate
```

The loader prints the resolved host/db/user (never the password) at startup so a misconnection is obvious. README documents this block. (Optional: a `--db-url` passthrough flag is acceptable but the env block is the documented path.)

## 6. Load pipeline

**Phase 0 — prep**
1. Resolve snapshot dir. Print resolved DB host/db/user (not password).
2. **Schema reconciliation (§4a):** `CREATE EXTENSION IF NOT EXISTS pg_trgm;`; for each `domains` column whose type ≠ `legal_domain[]` (check `information_schema.columns`), `ALTER … TYPE legal_domain[] USING domains::legal_domain[]` (safe — tables empty after truncate); apply `scripts/migration_search_v5_fixed.sql` (idempotent — installs `search_law()` + trgm index). Run this **before** load so inserts hit the final column types.
3. If `--truncate`: `DROP INDEX IF EXISTS idx_law_documents_tsv, idx_law_chunks_tsv` (recreate at end for bulk-insert speed), then `TRUNCATE law_chunks, law_relations, law_documents`. Else abort if any target table is non-empty.
   - **Crash-safety (review #7):** index recreation (Phase 4 step 1) runs in a `finally`/`atexit` path so an abort between Phase 0 and Phase 4 still rebuilds the tsv GIN indexes; all index ops use `IF EXISTS` / `IF NOT EXISTS`.

**Phase 1 — current config**
1. Load `metadata.parquet` fully into `meta_by_id: dict[int, row]` (~153K, small).
2. Stream `content.parquet`; **group rows by `int(id)`** (content.id is a numeric string → cast), concatenating `content_html` so multi-row docs become one. **Fragment handling (review #8):** dedup byte-identical fragments first (some duplicate ids are exact repeats); for distinct fragments, parquet does not guarantee cross-row-group order, so the loader treats **single-read row order as authoritative** (pyarrow preserves file row order within one read) and documents this — there is no stable per-fragment ordinal in the dataset to sort on. Accumulate into `dict[int, list[str]]` (full content parquet is 412M, fits a single read; concat in memory).
3. For each unique doc id:
   - `clean_html` → plaintext. Skip if cleaned text < 100 chars.
   - Enrich from `meta_by_id` if present (146,857), else stub. **Stub must set every NOT NULL column (review #4):** `title` = first non-empty line of cleaned text (truncated ~300 chars), fallback `"Văn bản VBPL-{id}"` — `title` is NOT NULL; `law_number="VBPL-{id}"`, `issuer="Chưa xác định"`, `law_type=other`, `status=active`, `domains=detect_domains(...)` (≥`['other']`).
   - Build `law_documents` row: `law_number = so_ky_hieu`, `law_type = map_law_type_vi(loai_van_ban)`, `issuer = co_quan_ban_hanh or "Chưa xác định"`, `signer = nguoi_ky`, dates via `parse_date_vi`, `status = map_status_vi`, `domains = detect_domains`, `full_text = plaintext`, `source_site = "huggingface/th1nhng0"`, `source_url = "https://vbpl.vn/.../{id}"` (or store id for traceability), `word_count`, `article_count`.
   - `INSERT … RETURNING id` → record `id_map[int_id] = uuid`. tsv = `to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(full_text,''))` (coalesce avoids NULL-poison when `full_text` is NULL — review #6).
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

**Phase 4 — finalize** (index recreation runs in the `finally` path per Phase 0 crash-safety)
1. Recreate GIN tsv indexes `IF NOT EXISTS` (if dropped).
2. `ANALYZE law_documents; ANALYZE law_chunks; ANALYZE law_relations;`.
3. Print counts (docs / chunks / relations). Verify search in two layers:
   - **Schema-agnostic tsv sanity (always):** direct `SELECT count(*) FROM law_chunks WHERE tsv @@ to_tsquery('simple', 'lao & động')` > 0 → proves tsv populated, independent of `search_law()` existing.
   - **End-to-end (now valid because Phase 0 deployed `search_law()` + reconciled `legal_domain[]`):** run 2–3 queries through `search_law()` (e.g. "hợp đồng lao động", "thuế giá trị gia tăng"), with and without a `domains` filter, asserting non-empty → proves the live `main.py` search path works on this DB. If `--limit` was set, skip the relationship assertion (see §7).

## 7. Performance

- Full merged load is large (~hundreds of thousands of docs, multi-million chunks; legacy content parquet ~3.3GB uncompressed). Expect a long run (likely hours) and tens of GB of DB + GIN index.
- Mitigations: drop GIN tsv indexes during load + recreate after; `execute_values` with `page_size≈1000`; commit every N docs; stream content in row-group batches where memory pressure appears.
- `--limit` validates the doc/chunk pipeline end-to-end on a subset. **Caveat (review #5):** Phase 3 still streams the full `relationships.parquet` but resolves against the small `id_map`, so a `--limit` run resolves ~0 edges **by design** — this is not a relationship bug. The documented validation command pairs them: `--limit N --skip-relations`. Relationship loading is only meaningfully exercised on a full (un-limited) run.

## 8. Dependencies & hygiene

- `requirements.txt`: add `pyarrow`, `beautifulsoup4`, `lxml`.
- `.gitignore`: add `data/datasets-legal-docs/` and `data/*.json` snapshots (400M+ — keep out of git).
- `.dockerignore`: add `data/datasets-legal-docs/` (Dockerfile does `COPY data/ ./data/` — without this the image bloats by multiple GB). Loader runs on host, not in the app image, so the dataset is never needed inside the container.
- README: add an "Offline dataset load" section documenting the §5.3 host-env block + `python scripts/load_offline_dataset.py --truncate` as the primary populate path; note the loader auto-applies schema reconciliation (`pg_trgm`, `legal_domain[]`, `search_law()` via `migration_search_v5_fixed.sql`) and that the crawler is now optional.
- No new Python deps for the schema reconciliation — `pg_trgm` / `legal_domain[]` / `search_law()` are DB-side (psycopg2 executes the SQL); only `pyarrow`, `beautifulsoup4`, `lxml` are added.

## 9. Testing

- Unit tests (`pytest`) for `_law_ingest.py`: `clean_html` (strips tags, keeps text), `chunk_document` (splits by Điều, sub-chunks long articles), the four enum maps (cover every observed input value + unknown→`other`/`active`), `normalize_doc_number`, `parse_date_vi` (both date formats + garbage).
- Integration smoke (optional, gated on a DB): run loader with `--limit 50 --truncate --skip-relations` against a test DB, assert `law_documents`/`law_chunks` counts > 0, `tsv` non-null, the direct `tsv @@ to_tsquery('simple', …)` sanity returns rows, and (Phase 0 having deployed it) a `search_law()` query returns rows. **Do not assert relationship count under `--limit`** (review #5). Also add a unit test that `detect_domains` output ⊆ the 12 `legal_domain` labels for a sample of titles (guards review #2).
- CI already imports `src.rag.search` + `src.agents.legal_agent`; no change needed (loader is a script, not imported by the app).

## 10. Risks / open notes

- **Memory:** grouping current content in memory (412M parquet → larger uncompressed). If pressure: two-pass (first pass id→row-offsets, second pass per-id). Start simple (single read), escalate only if needed.
- **Run time:** full legacy load is the heavy part; `--skip-legacy` gives a fast current-only corpus (149K docs) if needed.
- **Dedup fidelity:** same law may have slightly different number formatting across crawls → `normalize_doc_number` reduces but won't eliminate all dupes; acceptable (non-unique index).
- **Date parsing:** Vietnamese date fields are free-form strings; failures → NULL date (not fatal).
