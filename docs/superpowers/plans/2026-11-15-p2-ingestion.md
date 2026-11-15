# P2 Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every uploaded version runs through scan → parse → summarise → near-dup check → chunk → embed → index, with per-stage status in My uploads, ending with ACL-tagged chunks searchable in Elasticsearch.

**Architecture:** One `ingest` job per version (P1 queue). The handler runs stages in order, persisting `document_versions.stage` after each so retries resume and users see progress (ADR-0005). Parsing is delegated to the docling-serve sidecar (GROBID optional); models are reached over the OpenAI-spec API (local dev: Ollama — `embeddinggemma` 768-d + `qwen2.5:14b` on the game-01 RTX 4090 via SSH tunnel `localhost:21434`, or a local Ollama on `:11434`; prod: site endpoint). Parsed Docling JSON is stored in MinIO so the index can be rebuilt without re-parsing (ADR-0001).

**Tech Stack:** P1 stack + `openai` (client only), `elasticsearch` ≥ 8.15, `docling-core[chunking]` (HybridChunker; tokenizer files only, no torch), `datasketch` (MinHash), `lxml` (GROBID TEI). Containers: `elasticsearch:8.15+` (single node, security off in dev), `docling-serve`, optional `grobid`, optional `clamav`.

**Spec:** `docs/spec.md` §4 (steps 1–8), §3 (ACL fields), ADR-0002, 0005, 0006, 0008.

## Global Constraints

- Stages, in order and spelled exactly: `uploaded`, `scanned`, `parsed`, `summarized`, `deduped`, `chunked`, `embedded`, `searchable`; plus terminal `review` (waiting for near-dup decision), `rejected` (malware), `cancelled`.
- User-facing stage labels (My uploads): Scanned, Parsed, Chunked, Embedded, Searchable (summarized/deduped report as Parsed; `review` reports "Needs your decision").
- Scan is skipped (stage still advances) when `ROR_CLAMAV_HOST` is empty. Malware → stage `rejected`, job `failed` immediately (non-retryable), error `malware detected: <signature>`.
- Near-dup: MinHash 128 permutations over 5-word shingles of normalized text; threshold `ROR_NEAR_DUP_THRESHOLD` default `0.85` estimated Jaccard; candidates only among **latest versions of documents visible to the uploader** (P1 `visible_documents`), excluding the same document.
- Chunker: Docling `HybridChunker`, `max_tokens = 480`, `merge_peers = True`, tokenizer from `ROR_EMBED_TOKENIZER` (HF repo id or local path; default `sentence-transformers/all-MiniLM-L6-v2` — ungated, conservative for EmbeddingGemma's 2048-token window; prod: mirrored tokenizer of the configured model).
- Embedding input = `f"title: {title} | text: {chunker.contextualize(chunk)}"` (EmbeddingGemma document prompt); queries use `f"task: search result | query: {q}"` (P3). Batch size 32. Vector size must equal `ROR_EMBED_DIMS` (dev 768) or the job fails with `embedding dimension mismatch`.
- Elastic: index `chunks-<model-slug>-1` behind alias `chunks` (slug = lowercase, non-alphanumerics → `-`). `dense_vector` dims = `ROR_EMBED_DIMS`, `similarity: cosine`, `index_options.type: int8_hnsw`.
- Each chunk document carries ACL fields exactly: `owner_id`, `visibility`, `org_unit`, `share_users`, `share_groups` (ADR-0002), plus `doc_id`, `version_id`, `version`, `is_latest`, `chunk_id` (`{version_id}:{n}`), `title`, `authors`, `year`, `text`, `headings`, `pages` (int[]), `bboxes` (object, not indexed), `embedding_model`.
- Derived objects: `derived/{version_id}/docling.json` in the same bucket.
- Summary prompt output ≤ 3 sentences; inputs truncated to the first 6000 characters of document text.

## Review Focus

1. Worker crash mid-pipeline → retry resumes from the last persisted stage, does not duplicate chunks (index ops use deterministic `_id = chunk_id`) — Task 7.
2. Near-dup must never compare against documents the uploader cannot see — Task 5 (test with a private doc of another user).
3. Docling returns no text (blank scan / image-only without OCR result) → job fails with `no text extracted`, not an empty searchable doc — Task 4.
4. Inference endpoint down → retryable failure with backoff (not `rejected`), stage unchanged — Task 6.
5. Re-upload as new version → previous version's chunks get `is_latest=false` — Task 7.

---

## File structure

```
deploy/dev/compose.yaml                 + elasticsearch, docling, (profile full) grobid, clamav
backend/src/roracle/config.py           + elastic_url, llm_base_url, llm_api_key, embed_model, embed_dims,
                                          embed_tokenizer, summary_model, docling_url, grobid_url, clamav_host,
                                          near_dup_threshold
backend/src/roracle/clients.py          llm(), es() factories
backend/src/roracle/queue.py            + NonRetryable exception honoured by fail()
backend/migrations/versions/0002_ingest.py
backend/src/roracle/ingest/__init__.py  STAGES, handle_ingest(session, payload)
backend/src/roracle/ingest/scan.py      clamd INSTREAM client
backend/src/roracle/ingest/parse.py     docling-serve client, GROBID TEI → metadata
backend/src/roracle/ingest/summary.py   summarize(text, title) -> str
backend/src/roracle/ingest/neardup.py   minhash(text) -> bytes, find_near_duplicate(...)
backend/src/roracle/ingest/chunking.py  chunk(docling_json, title) -> list[Chunk]
backend/src/roracle/ingest/embed.py     embed(texts) -> list[list[float]]
backend/src/roracle/search_index.py     ensure_index(), index_chunks(), mark_previous_versions()
backend/src/roracle/api/routes/documents.py  + POST /documents/{doc_id}/versions/{version_id}/near-dup
backend/tests/fixtures/                 sample.md, sample.pdf (tiny, generated), docling.json, grobid_tei.xml
```

---

### Task 1: Configuration, clients, dev services, non-retryable failures

**Files:** Modify `config.py`, `queue.py`, `deploy/dev/compose.yaml`, `backend/pyproject.toml`; Create `clients.py`; Test `tests/test_queue.py` (extend), `tests/test_clients.py`.

**Interfaces:**
- Produces: Settings fields (names above; defaults: `elastic_url="http://localhost:9200"`, `llm_base_url="http://localhost:11434/v1"`, `llm_api_key="unused"`, `embed_model="embeddinggemma"`, `embed_dims=768`, `embed_tokenizer="sentence-transformers/all-MiniLM-L6-v2"`, `summary_model="qwen2.5:3b"`, `docling_url="http://localhost:5001"`, `grobid_url=""`, `clamav_host=""`, `near_dup_threshold=0.85`); `clients.llm(settings) -> openai.OpenAI`; `clients.es(settings) -> Elasticsearch`; `queue.NonRetryable(Exception)` — `fail()` marks `failed` immediately when the handler raised it.

- [ ] **Step 1:** Compose: `elasticsearch` (`discovery.type=single-node`, `xpack.security.enabled=false`, `ES_JAVA_OPTS=-Xms1g -Xmx1g`, port 9200), `docling` (`quay.io/docling-project/docling-serve`, port 5001), and under profile `full`: `grobid` (8070), `clamav` (3310).
- [ ] **Step 2: Failing tests:** `test_non_retryable_fails_immediately` (attempt 1, `NonRetryable("x")` → status `failed`); `test_clients_use_settings` (base_url / hosts taken from Settings).
- [ ] **Step 3–4:** Implement; run → PASS.
- [ ] **Step 5: Commit** `feat(ingest): add ingest settings, clients and dev services`

### Task 2: Schema for ingest state

**Files:** Create `migrations/versions/0002_ingest.py`; Modify `models.py`; Test `tests/test_schema.py` (extend).

**Interfaces:**
- Produces on `DocumentVersion`: `stage: str = "uploaded"` (CHECK in the stage list), `page_count: int | None`, `summary: str | None`, `meta: JSONB = {}` (title, authors[], year, venue, doi, abstract), `references: JSONB = []` (each `{title, authors[], year, doi}`), `minhash: bytes | None`, `near_dup_of: UUID | None` (FK document_versions), `is_latest: bool = True`. `ingest.set_stage(session, version, stage) -> None` (sets and commits; the only place that commits mid-job).
- Because `set_stage` commits mid-job, the claimed job's `running` status becomes durable; a crashed worker would leave it `running` forever. Add `queue.requeue_stale(session, older_than_s: int = 900) -> int` (sets `running` jobs whose `updated_at` is older than the cutoff back to `queued`, `run_after = now()`), touch `jobs.updated_at` in `set_stage` (heartbeat), and call `requeue_stale` from `run_forever` every 60 s. Test: a `running` job with `updated_at` 20 min old is requeued; a fresh one is not.
- [ ] Tests: stage CHECK rejects `"bogus"`; defaults as listed. Implement, pass, `alembic check` clean.
- [ ] **Commit** `feat(ingest): add ingest state to document versions`

### Task 3: Malware scan stage

**Files:** Create `ingest/scan.py`; Test `tests/test_scan.py`.

**Interfaces:** `scan(stream: BinaryIO, host: str, port: int = 3310, timeout: float = 30) -> str | None` — clamd `zINSTREAM` (4-byte big-endian chunk lengths, zero-length terminator); returns signature name on `FOUND`, `None` on `OK`; raises `ConnectionError` on transport failure (retryable).
- [ ] Tests against an in-test fake clamd (`socketserver` on an ephemeral port): clean → `None`; `stream: Eicar-Test-Signature FOUND` → `"Eicar-Test-Signature"`; server closes early → `ConnectionError`.
- [ ] **Commit** `feat(ingest): add clamd scan client`

### Task 4: Parse stage (Docling + optional GROBID)

**Files:** Create `ingest/parse.py`, `tests/fixtures/docling.json` (captured from docling-serve for `sample.pdf`), `tests/fixtures/grobid_tei.xml`; Test `tests/test_parse.py`.

**Interfaces:**
- `convert(data: bytes, filename: str, docling_url: str) -> dict` — POST `{docling_url}/v1/convert/file` (multipart `files`, form `to_formats=json`, `do_ocr=true`); returns the DoclingDocument JSON (`document.json_content`); raise `NonRetryable("no text extracted")` if it has no text items.
- `document_text(doc: dict) -> str` (reading-order concatenation of text items).
- `grobid_metadata(pdf: bytes, grobid_url: str) -> tuple[dict, list[dict]]` — `processFulltextDocument`, parse TEI with lxml into `meta` and `references` shapes (Task 2).
- [ ] Tests: `document_text` on fixture JSON contains a known sentence; TEI fixture → title, ≥ 2 authors, DOI, ≥ 3 references with titles; `convert` against an `httpx.MockTransport` returning a doc with zero texts → `NonRetryable`. Integration test (`@pytest.mark.integration`, skipped unless docling reachable): real `sample.md` converts and yields text.
- [ ] **Commit** `feat(ingest): parse documents with docling and grobid`

### Task 5: Summary and near-duplicate check

**Files:** Create `ingest/summary.py`, `ingest/neardup.py`; Modify `api/routes/documents.py`; Test `tests/test_neardup.py`, `tests/test_summary.py`.

**Interfaces:**
- `summarize(client, model: str, title: str, text: str) -> str` (system prompt: "Summarize this research document in at most 3 sentences for a search index. Plain text."; user content = title + first 6000 chars).
- `minhash(text: str) -> bytes` (datasketch `MinHash(num_perm=128)`, 5-word shingles of lowercase alnum-only words; serialized `hashvalues` as bytes).
- `similarity(a: bytes, b: bytes) -> float`.
- `find_near_duplicate(session, principal: Principal, version: DocumentVersion, threshold: float) -> DocumentVersion | None` — best match ≥ threshold among latest versions of other documents in `visible_documents(principal)` with non-null `minhash`. `# ponytail: linear scan over visible versions; move to LSH buckets in Elastic when > ~50k docs per user scope.`
- When found: `near_dup_of = match.id`, stage `review`, handler returns (job `done`); My uploads shows "Needs your decision".
- `POST /documents/{doc_id}/versions/{version_id}/near-dup` body `{"action": "new_version" | "separate" | "cancel"}` (owner only, version must be in `review`): `new_version` → move this version under the matched document as `max(version)+1`, mark old latest `is_latest=false`, delete the now-empty document row, re-enqueue ingest (resumes at `deduped`); `separate` → stage `deduped`, re-enqueue; `cancel` → stage `cancelled`, document soft-deleted, blob refcount − 1.
- [ ] Tests: identical texts ≥ 0.99; unrelated < 0.2; private doc of another user never matched; each action's state transition; non-owner → 404 (no existence leak); summary uses a fake client and returns its text.
- [ ] **Commit** `feat(ingest): summarize and detect near-duplicates with user decision`

### Task 6: Chunk and embed

**Files:** Create `ingest/chunking.py`, `ingest/embed.py`; Test `tests/test_chunking.py`, `tests/test_embed.py`.

**Interfaces:**
- `@dataclass Chunk(n: int, text: str, contextual: str, headings: list[str], pages: list[int], bboxes: list[dict])`
- `chunk(doc: dict, title: str, tokenizer: str, max_tokens: int = 480) -> list[Chunk]` — `DoclingDocument.model_validate(doc)`, `HybridChunker(tokenizer=HuggingFaceTokenizer.from_pretrained(tokenizer, max_tokens=max_tokens), merge_peers=True)`; pages/bboxes from each chunk's `meta.doc_items[*].prov`; `contextual = f"{title}\n{chunker.contextualize(c)}"`.
- `embed(client, model: str, texts: list[str], dims: int, batch: int = 32) -> list[list[float]]` — raises `NonRetryable("embedding dimension mismatch")` on wrong size; transport errors propagate (retryable).
- [ ] Tests: fixture doc → ≥ 1 chunk, every chunk has ≥ 1 page, contextual starts with title, no chunk exceeds 480 tokens; embed batches 70 texts into 3 calls (fake client), wrong dims → NonRetryable; `openai.APIConnectionError` propagates.
- [ ] **Commit** `feat(ingest): chunk with docling hybrid chunker and embed`

### Task 7: Index and the ingest handler

**Files:** Create `search_index.py`, `ingest/__init__.py`; Modify `worker.py` (register `ingest`), `api/routes/me.py` (add `stage`, `page_count`, `near_dup_of`); Test `tests/test_search_index.py`, `tests/test_ingest_handler.py`.

**Interfaces:**
- `index_name(model: str) -> str`; `ensure_index(es, model: str, dims: int) -> str` (creates index + alias `chunks` if absent; mapping per Global Constraints; `text` and `title` analyzed `english`, ACL fields `keyword`).
- `acl_fields(session, document) -> dict` (owner_id, visibility, org_unit, share_users, share_groups from `document_shares`).
- `index_chunks(es, version, document, chunks, vectors, acl, model) -> int` (bulk, `_id = chunk_id`, refresh `wait_for`).
- `mark_previous_versions(es, doc_id, latest_version_id)` (update_by_query `is_latest=false`).
- `handle_ingest(session, payload, *, settings, store, es, llm) -> None`: loads version + document; runs only stages after the persisted one; `set_stage` after each; final stage `searchable`. Worker registers `HANDLERS = {"ingest": partial(handle_ingest, settings=…, store=…, es=…, llm=…)}`.
- [ ] Tests (integration against compose ES, fake LLM/docling): full run reaches `searchable` and `chunks` alias returns the chunks with ACL fields; re-run after simulated crash at `chunked` produces no duplicates (same count); second version flips `is_latest` on the first; `/me/uploads` shows `stage`.
- [ ] **Commit** `feat(ingest): index chunks and wire ingest pipeline into worker`

### Task 8: End-to-end check on the real local stack

**Files:** Create `backend/tests/test_e2e_ingest.py` (`@pytest.mark.e2e`, skipped unless `ROR_E2E=1`), `scripts/dev-up.sh`.

- [ ] `scripts/dev-up.sh`: `docker compose -f deploy/dev/compose.yaml up -d`, wait for Postgres/MinIO/ES/Keycloak/docling health, run migrations.
- [ ] Test: upload `fixtures/sample.md` via API with a test token, run `run_once` until idle (max 60 s), assert stage `searchable`, ES has ≥ 1 chunk with a 768-d vector from the configured endpoint, summary non-empty.
- [ ] Run with `ROR_E2E=1 uv run pytest -m e2e -v` → PASS. **Commit** `test(ingest): end-to-end ingest against local stack`
