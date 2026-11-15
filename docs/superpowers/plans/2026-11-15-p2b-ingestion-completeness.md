# P2b Ingestion Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the spec §4 gaps left by P2 (ledger ruling R18): office files are converted to PDF by Gotenberg before Docling (the reader gets the PDF rendition), the document summary is embedded and used for near-dup detection and document-level ("papers about X") search, the summary joins the chunk context prefix, and identical chunk prompts are never re-embedded. Plus three P3 final-review carry-overs.

**Architecture:** Everything stays inside the existing `parse` → `summarized` → `deduped` → `chunked` → `embedded` → `searchable` stages (no new stage names). Office→PDF happens inside `parse`; the PDF rendition is a derived object next to `docling.json`, the original blob is untouched. The summary vector is stored in Postgres (`document_versions.summary_embedding`, float32 bytes, like `minhash`) for near-dup, and copied onto **chunk 0 only** of each version in Elastic (`summary_embedding` dense_vector) so all existing ACL/tag/is_latest update_by_query paths reach it unchanged; retrieval adds a third kNN list on that field to RRF. Embedding reuse is keyed by `prompt_sha = sha256(prompt)` stored on each chunk (index is per model), looked up only among chunks of the **same document**.

**Tech Stack:** P2/P3 stack + Gotenberg 8 (`gotenberg/gotenberg:8.20.1`, LibreOffice route).

**Spec:** `docs/spec.md` §4 steps 2, 3, 5, 7; ADR-0005, 0006, 0008. P2 plan Global Constraints still apply except where overridden below.

## Global Constraints

- Stage names unchanged (P2 list). R9: all `document_versions`/`documents` writes during ingest via `ingest/state.py` helpers (`update_version`, `set_stage`, …). Do not restructure `ingest/state.py`, `acl_sync.py`, or the post-index recheck block in `pipeline._index` (P3 fix wave is editing them; controller rebases).
- Office extensions (lowercase, from filename): `doc docx odt rtf ppt pptx odp xls xlsx ods`. Converted only when `ROR_GOTENBERG_URL` is set; empty → office files go to Docling as before (Docling reads docx/pptx/xlsx natively).
- Gotenberg call: `POST {url}/forms/libreoffice/convert`, multipart field `files` = (original filename, bytes); response body = PDF (must start with `%PDF-`). 400/413/415/422 → `NonRetryable("document could not be converted (unsupported or invalid file)")`; 5xx/transport → retryable. Timeout `ROR_GOTENBERG_TIMEOUT_S` (default 300).
- Rendition object: `derived/{version_id}/rendition.pdf` (`application/pdf`). Docling and GROBID receive the PDF (filename `<stem>.pdf`), so page/bbox provenance refers to the rendition.
- Summary embedding: `embed(llm, embed_model, [doc_prompt(title, summary)], embed_dims)` → stored as `np.float32` little-endian bytes (`"<f4"`) in `document_versions.summary_embedding` (nullable `LargeBinary`, migration `0005_p2b`). Empty summary → `None`. Computed in the same `update_version` call that sets `summary` + `stage="summarized"`; versions reaching `_index` without one get it computed there (lazy backfill).
- Near-dup: match if MinHash Jaccard ≥ `ROR_NEAR_DUP_THRESHOLD` (0.85) **or** summary cosine ≥ `ROR_NEAR_DUP_SUMMARY_THRESHOLD` (default 0.97). Best candidate = highest `max(jaccard, cosine)` among those passing. Vectors of different length are never compared (cosine 0). Candidate set unchanged (visible latest versions of other documents).
- Embedding prompt (overrides P2): `doc_prompt(title, text, summary=None)` → `f"title: {title or 'none'} | text: {summary}\n\n{text}"` when summary is non-empty, else the P2 form. Queries unchanged.
- ES fields added via `search_index._mapping` (ensure_index must backfill them on existing indices): `prompt_sha` (keyword, every chunk), `summary_embedding` (dense_vector, same dims/similarity/int8_hnsw as `embedding`, chunk n=0 only, omitted when no summary vector). ACL fields and `acl_filter` untouched.
- Reuse: before embedding, look up `prompt_sha` values of the new prompts in ES restricted to `term doc_id = <this document>` (never other documents: a cache hit across documents is a timing oracle for invisible content, spec §4 "never leak existence"); reuse `_source.embedding` for hits (still dimension-checked), embed only misses, in original order.
- Retrieval: `search_chunks` gains a third list — kNN on `summary_embedding` with the same query vector, same filter, `k = size`, `num_candidates = 4 × size` — fused with RRF alongside BM25 and chunk kNN. A missing field/no hits is a normal empty list.
- Existing indexes need re-embedding after this change (prompt changed): documented operator procedure, no migration framework.

## Review Focus

1. Gotenberg unavailable → retryable failure, stage stays `scanned`; bad office file → NonRetryable; original blob never replaced (download still returns the original bytes) — Task 1.
2. The rendition endpoint returns 404 `{"detail":"not found"}` for invisible docs and for versions with neither rendition nor PDF original; inline PDF headers keep `nosniff` + sandbox CSP — Task 2.
3. Near-dup by summary vector never compares against invisible documents — Task 3.
4. Summary vector lands only on chunk 0, survives ACL sync, and the doc-level kNN list respects `acl_filter` + is_latest (R19 post-filter still applies at consumers) — Task 4.
5. Embedding reuse never reads vectors from another document — Task 5.

---

### Task 1: Gotenberg office → PDF in the parse stage

**Files:** Create `backend/src/roracle/ingest/office.py`; Modify `config.py` (`gotenberg_url: str = ""`, `gotenberg_timeout_s: float = 300.0`), `ingest/pipeline.py` (`_parse` only), `deploy/dev/compose.yaml` (service `gotenberg`, image `${REGISTRY_MIRROR:-}gotenberg/gotenberg:8.20.1`, profile `full`, port `${ROR_GOTENBERG_PORT:-3300}:3000` (host 3000 is taken on dev boxes), healthcheck `curl -sf http://localhost:3000/health`), `docs/enterprise/airgap-mirror-manifest.md` (pin `8.20.1`), `docs/enterprise/deployment-guide.md` (env vars, sizing line, behaviour); Test `tests/test_office.py`, extend `tests/test_ingest_handler.py`.
**Interfaces:**
- `OFFICE_EXTS: frozenset[str]`; `is_office(filename: str) -> bool`; `rendition_key(version_id) -> str` (`derived/{id}/rendition.pdf`).
- `to_pdf(data: bytes, filename: str, url: str, timeout: float = 300.0, client: httpx.Client | None = None) -> bytes` (contract in Global Constraints; non-PDF 200 body → `ValueError`, retryable).
- `_parse`: if `settings.gotenberg_url and is_office(v.filename)`: `pdf = to_pdf(...)`, `store.put_bytes(rendition_key(v.id), pdf, "application/pdf")`, then Docling/GROBID get `pdf` and filename `<stem>.pdf`; `is_pdf` true for GROBID.
- [ ] Unit (httpx.MockTransport): docx → PDF bytes; 422 → NonRetryable; 503 → `httpx.HTTPStatusError`; non-PDF body → ValueError; `is_office` cases incl. uppercase `.DOCX` and `.pdf` false.
- [ ] Handler (integration, monkeypatched `to_pdf` + fake convert): docx version stores rendition, convert receives PDF bytes + `.pdf` name; gotenberg_url empty → no rendition, original sent.
- [ ] Integration `test_office.py::test_real_gotenberg` (`@pytest.mark.integration`, skip unless `GET {ROR_GOTENBERG_URL or http://localhost:3300}/health` is 200): convert a tiny docx built with `zipfile` (minimal WordprocessingML) → `%PDF-`.
- [ ] **Commit** `feat(ingest): convert office files to PDF with gotenberg`

### Task 2: Reader PDF endpoint

**Files:** Modify `api/routes/documents_read.py` (and `detail` response); Test extend `tests/test_documents_read.py`.
**Interfaces:** `GET /documents/{doc_id}/versions/{n}/pdf` → inline `application/pdf`: the rendition if `rendition_key(v.id)` exists, else the original blob when it is a PDF (same sniff rule as `/file`), else 404 `not found`. Headers: `Content-Disposition: inline` (filename `<stem>.pdf`), `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox; default-src 'none'`. Visibility via existing `_version`. `detail` versions[] gain `"has_pdf": bool` only if it is cheap (stage-based: office + stage ≥ parsed, or PDF original) — otherwise skip with a ruling. `/file` unchanged (always the original).
- [ ] Tests: rendition served for docx; PDF original served when no rendition; md original → 404; invisible doc → 404 body `{"detail":"not found"}`; headers asserted.
- [ ] **Commit** `feat(documents): serve PDF rendition for the reader`

### Task 3: Summary embedding + near-dup by summary

**Files:** Create `migrations/versions/0005_p2b_summary_embedding.py` (revision id `0005_p2b`, down `0004`; controller re-chains on merge — other teams own `0005`); Modify `models.py`, `config.py` (`near_dup_summary_threshold: float = 0.97`), `ingest/neardup.py`, `ingest/embed.py` (`to_bytes(vec) -> bytes`, `from_bytes(b) -> np.ndarray`), `ingest/pipeline.py` (summarize block in `handle_ingest`, `_dedupe`); Test extend `tests/test_neardup.py`, `tests/test_embed.py`, `tests/test_schema.py`, `tests/test_ingest_handler.py`.
**Interfaces:**
- `cosine(a: bytes | None, b: bytes | None) -> float` (0.0 for None/empty/length mismatch/zero norm).
- `find_near_duplicate(session, principal, version, threshold, summary_threshold) -> DocumentVersion | None`; query no longer requires `minhash IS NOT NULL` alone — candidates with either minhash or summary_embedding; early-return only when version has neither.
- [ ] Tests: bytes round-trip; cosine identical = 1, orthogonal = 0, mismatch = 0; different text but identical summary vector → matched; invisible doc with identical vector → not matched; summarize stage persists summary_embedding (FakeLLM); empty summary → None; migration upgrade/downgrade + `alembic check` clean.
- [ ] **Commit** `feat(ingest): embed document summary and use it for near-dup`

### Task 4: Document-level search via summary vector

**Files:** Modify `search_index.py` (`_mapping`, `ensure_index` backfill of new non-keyword fields, `index_chunks(..., summary_vector: list[float] | None = None)` sets `summary_embedding` on n=0), `ingest/pipeline.py` (`_index`: pass vector; lazily compute+persist via `update_version` when missing and summary present), `retrieval.py` (third list); Test extend `tests/test_search_index.py`, `tests/test_retrieval.py`, `tests/test_retrieval_es.py`.
**Interfaces:** `ensure_index` adds any missing field from `_mapping` whose type is `keyword` or `dense_vector` (put_mapping); dims check also covers `summary_embedding` when present. `search_chunks` fuses `[bm25, knn, summary_knn]`.
- [ ] Tests: chunk 0 has `summary_embedding`, chunk 1 does not; pre-existing index without the fields gets them on `ensure_index`; unit (fake ES) third list participates in RRF; integration: doc whose only match is the summary vector is found, invisible doc with the same vector is not.
- [ ] **Commit** `feat(search): index summary vector for document-level retrieval`

### Task 5: Summary in the chunk prompt + exact-dup embedding reuse

**Files:** Modify `ingest/embed.py` (`doc_prompt` signature, `prompt_sha(prompt) -> str`), `search_index.py` (`index_chunks(..., prompt_shas: list[str] | None = None)` writes `prompt_sha`; `cached_vectors(es, model, doc_id, shas: list[str]) -> dict[str, list[float]]`, batches of 1000 terms, missing index → `{}`), `ingest/pipeline.py` (`_index` builds prompts with summary, reuses cached vectors, embeds misses only), `docs/enterprise/operations-runbook.md` (re-embed procedure: delete `chunks-<slug>-1`, reset searchable versions to `deduped` + enqueue `ingest` jobs — exact SQL; note non-owners lose visibility during the run), `docs/enterprise/deployment-guide.md` (`ROR_NEAR_DUP_SUMMARY_THRESHOLD`); Test extend `tests/test_embed.py`, `tests/test_search_index.py`, `tests/test_ingest_handler.py`.
- [ ] Tests: `doc_prompt` with/without summary exact strings; re-running `_index` on the same version calls the embedder 0 times for unchanged chunks (FakeLLM call counter); a new version of the same document with one changed chunk embeds exactly 1 prompt; a **different** document with identical text embeds all prompts (no cross-document reuse); cached vectors with wrong dims → treated as miss.
- [ ] **Commit** `feat(ingest): summary context in chunk prompts and embedding reuse`

### Task 6: P3 carry-overs

**Files:** Modify `api/routes/near_dup.py` (`_merge_library`), `api/routes/documents.py` (upload `org_unit = normalize_path(...)` from `documents_write`); Test extend `tests/test_neardup.py` or the near-dup API test, `tests/test_upload.py`.
- [ ] `_merge_library`: `superseded_by == old` re-pointed to `new` (content moved) instead of nulled; a vote on `new` itself that would become self-referential gets `superseded_by=None, reason=None`.
- [ ] Upload: group path normalized exactly like PATCH (`/Math/` → `/Math`). Test asserts stored `org_unit`.
- [ ] R2 `delete_document_chunks`: no P2b path deletes chunks of a whole document → not added (ruling in ledger).
- [ ] **Commit** `fix: carry P3 review items (superseded re-point, upload org_unit)`
