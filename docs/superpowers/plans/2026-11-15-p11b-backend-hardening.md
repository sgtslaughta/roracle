# P11b Backend Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the P11a "backend gaps" (TLS/CA, credentials, migrate wait, readiness, metrics/OTel, ClamAV port, upload spooling), enforce rate limits, add retention + maintenance + embedding-model switch jobs, and fill the P7/P8/P10 web-contract gaps.

**Architecture:** All new knobs are `ROR_*` settings in `config.py`, wired into the Helm chart (`deploy/helm/roracle` values + `_helpers.tpl`) and `docs/enterprise/{deployment-guide,operations-runbook,security}.md` in the same task. Periodic work copies the `connector_tick` pattern (self re-enqueueing job + `ensure_*` under an advisory lock). No migration is planned; if one is needed: `revision="0011_p11b"`, `down_revision="0009"` (controller re-chains onto P9 0010 at rebase).

**Tech Stack:** P10 stack + `prometheus-client` (tiny, pure-python). OTel: optional extra `otel` (`opentelemetry-distro`, `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-fastapi`, `-sqlalchemy`, `-httpx`), installed in the image, activated only via `opentelemetry-instrument` when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

**Spec:** `docs/spec.md` §9–§11; P11a FINAL "Backend gaps for P11b".

## Global Constraints

- Tests: `export ROR_DATABASE_URL=postgresql+psycopg://roracle:roracle@localhost:55433/roracle_p11b_test` on every run; unit tests use fakes; integration/e2e only under `flock /tmp/roracle-integ.lock`. `uv run ruff check . && uv run ruff format --check .` clean. Files < 500 lines.
- Do not edit `backend/src/roracle/writing/`, drafts routes, `mcp/tools_writing.py` (parallel P9 team). Small additive edits to `config.py`/`app.py` are fine.
- Invariants: not visible ⇒ 404 `{"detail":"not found"}`; R19 PG post-filter; handlers never commit; R9 short sessions.
- Every new setting: default keeps current behaviour; documented in deployment-guide; wired in Helm values (`config:` map or `secrets.keys`).
- Paging convention for new list params: `?limit=&offset=` (limit default 50, max 200) → response gains `total`. Keyset lists keep `before_id`.

## Review Focus

1. Rate limiter keyed correctly (PAT vs JWT), 429 + `Retry-After`, never limits health/metrics — Task 3 (opus).
2. CA/TLS: custom CA used by ES, S3 (boto3) and httpx clients; no `verify=False` path; credentials never logged or exposed by `/readyz` — Task 1 (opus).
3. Upload streaming: no full-file disk spool in the API; sha256/size cap still enforced; partial S3 uploads aborted; dedup by content hash preserved — Task 3.
4. Purge/GC never deletes a blob still referenced by any version/evidence row — Task 5.
5. Re-embed: reads never see two generations at once; ACL/tag/delete updates during re-embed reach both indices; catch-up after cutover — Task 6 (opus).

## Rulings (plan-level)

- R1: Rate limit = in-process token bucket per API replica, key `pat:<id>` for PAT requests else `user:<id>`, capacity = `rate_limit_per_minute`, refill per minute; 0 = off. Multi-replica: effective cluster limit ≈ N × limit (documented; ingress limits for a hard global cap) — cost if wrong: limits loose by replica count; a PG counter would add a write per request.
- R2: Uploads stream from `request.stream()` through `python-multipart`'s streaming parser into an S3 multipart upload at `tmp/<uuid>` (8 MiB parts, hashing on the fly), then server-side copy to `blobs/<sha[:2]>/<sha>` (skip if exists) and delete tmp; abort on any error. API `/tmp` emptyDir drops to 1Gi. Worker keeps its tmp (parsing) — cost: copy doubles S3 write I/O once per upload.
- R3: `/readyz` = DB `SELECT 1` + ES ping + S3 `head_bucket`, each 2 s timeout, 503 with `{checks:{db,es,s3: "ok"|"fail"}}` (no error text). `/healthz` stays static (liveness). Worker serves `/healthz` + `/metrics` on `ROR_WORKER_HEALTH_PORT` (default 8001, 0 = off) via stdlib `http.server` thread; healthz 503 when the main loop heartbeat is older than 5 min.
- R4: Active embedding model is persisted (runtime override, not admin-PUTtable) and set only by re-embed cutover; env `ROR_EMBED_MODEL/_DIMS` is the bootstrap default.
- R5: Retention purges soft-deleted documents after `ROR_RETENTION_DELETED_DAYS` (default 0 = never) and GCs blobs with no referencing row (checked by `NOT EXISTS`, not refcount) older than 1 day.

---

### Task 1: Connectivity settings, CA trust, migrate --wait

**Files:** Modify `config.py`, `clients.py`, `storage.py`, `db.py`, `ingest/scan.py` caller(s), `__main__.py`; Helm `values.yaml`, `_helpers.tpl`, `migrate-job.yaml`; docs. Test `tests/test_config_p11b.py`, `tests/test_migrate_wait.py`.
**Interfaces:**
- Settings: `elastic_ca_certs: str = ""` (path), `elastic_username: str = ""`, `elastic_password: str = ""`, `s3_ca_bundle: str = ""` (path → boto3 `verify=`), `database_password: str = ""`, `clamav_port: int = 3310`.
- `database_url` validator: `postgresql://` / `postgres://` → `postgresql+psycopg://`; if `database_password` set, inject it into the URL (`sqlalchemy.engine.make_url(...).set(password=...)`, rendered with `hide_password=False`).
- `clients.es`: `basic_auth=(user, pw)` when username set, `ca_certs=` when set. `storage.blob_store_from`: `verify=s3_ca_bundle or None`. All clamd `scan(...)` calls pass `port=settings.clamav_port`.
- `roracle migrate [--wait SECONDS]`: retry `engine.connect()` every 5 s until the deadline, then exit non-zero with message; then upgrade head.
- Helm: `migrate-job.yaml` uses `migrate --wait 600` (drop inline python); `ROR_ELASTIC_CA_CERTS`/`ROR_S3_CA_BUNDLE` point at the merged bundle path when `caBundle` enabled; optional secret keys `elasticUsername/elasticPassword/databasePassword`; `ROR_CLAMAV_PORT` from `parsers.clamav.port`; remove the "always 3310" comment.
- [ ] Tests: URL normalisation cases (plain, `postgres://`, already `+psycopg`, password injection incl. special chars `p@ss:/`); es client built with basic_auth/ca_certs (inspect constructor kwargs via monkeypatch); boto3 client gets `verify`; migrate --wait retries then fails fast against a bad port (unit with monkeypatched `create_engine`). `helm template` with CI values still renders (`helm lint deploy/helm/roracle`).
- [ ] **Commit** `feat(config): CA bundles, separate ES/DB credentials, clamav port, migrate --wait`

### Task 2: Readiness, worker health, metrics, OTel

**Files:** Create `roracle/metrics.py`, `roracle/worker_health.py`; modify `api/routes/health.py`, `api/app.py` (middleware), `worker.py`, `queue.py` (counters), `pyproject.toml`; Helm probes/ServiceMonitor/worker port, `dashboards/roracle.json`, NOTES.txt; docs. Test `tests/test_health_metrics.py`.
**Interfaces:**
- `GET /readyz` per R3. `GET /metrics` (unauthenticated, Prometheus text; chart NetworkPolicy/ingress must not expose it publicly — ingress excludes `/metrics`).
- Metric names (final, dashboard uses them): `roracle_http_requests_total{method,route,status}`, `roracle_http_request_duration_seconds{method,route}` (histogram, route = matched template, not raw path), `roracle_jobs{kind,status}` (gauge, collected at scrape from `jobs` with a 2 s statement timeout), `roracle_jobs_oldest_queued_seconds{kind}`, `roracle_job_runs_total{kind,outcome}` (outcome done|retry|failed, worker), `roracle_ingest_stage_failures_total{stage}`.
- OTel: entrypoints run under `opentelemetry-instrument` only if `OTEL_EXPORTER_OTLP_ENDPOINT` set (in `__main__.py`: `os.execvp` wrapper when env set and module importable; otherwise plain). Optional extra `otel` added to pyproject; Dockerfile installs `--extra otel`.
- Helm: api readiness → `/readyz`; worker gets `containerPort 8001` + liveness `/healthz`; ServiceMonitor/PodMonitor (if chart has `metrics.serviceMonitor.enabled`) scrapes api+worker `/metrics`; dashboard panels switched from nginx/cnpg-custom queries to the names above (keep cnpg replication panels).
- [ ] Tests: readyz 200 with fakes, 503 + no secret text when ES fails; metrics endpoint contains each family; route label uses template (`/documents/{doc_id}`); worker health server returns 503 for a stale heartbeat.
- [ ] **Commit** `feat(ops): readiness, worker health, prometheus metrics, opt-in otel`

### Task 3: Ingress hardening — upload streaming and rate limits (opus review)

**Files:** Create `roracle/ratelimit.py`, `roracle/upload_stream.py`; modify `auth.py` (`current_principal`), `mcp/auth.py` (BearerAuth), `storage.py`, `api/routes/documents.py`, `notes/evidence.py` (+ its route); Helm api `tmpSize`; docs (security: rate limit semantics, R1 multi-replica). Tests `tests/test_ratelimit.py`, `tests/test_upload_stream.py`.
**Interfaces:**
- `TokenBucket.take(key, now) -> float | None` (None = allowed, else retry-after seconds); limit read from runtime-effective `rate_limit_per_minute` per request. Over limit → 429 `{"detail":"rate limited"}` + `Retry-After`. Same limiter for MCP bearer path.
- `upload_stream.receive(request, store, max_bytes) -> (fields: dict[str,str], StagedBlob(sha256, size, filename, content_type))` per R2; `BlobStore.put_stream` / `promote(tmp_key, sha)` / `abort`. Existing `stage()` stays for connectors/MCP.
- Document upload + evidence upload routes use it; existing form field names and responses unchanged; `TooLarge` → 413, empty → 422 as today.
- [ ] Tests: bucket allows N then 429 with Retry-After, refills; PAT and JWT for same user have separate buckets; 0 disables; health/metrics unaffected. Upload: fake S3 (moto not available → in-memory fake client recording parts); oversize aborts multipart and returns 413; duplicate content → no second blob; existing upload tests still pass. Integration (flock): real MinIO round-trip of a 20 MiB upload.
- [ ] **Commit** `feat(api): stream uploads to object storage and enforce per-token rate limits`

### Task 4: Web contract gaps (P7, P8, P10)

**Files:** Modify `handoff.py`, `api/routes/handoffs.py`, `api/routes/annotations.py`, `api/routes/proposals.py`, `notes/community.py`, `notes/evidence.py`, `api/routes/notifications.py`, `api/routes/admin.py`, `api/routes/admin_settings.py`, `api/routes/admin_connectors.py`. Tests extend the matching `tests/test_*.py`.
**Interfaces:**
- Handoff `view`: `documents: [{id,title}]` (visible ones only, same filter as `doc_ids`), `shares: [{kind, principal}]` (creator/admin only, else omitted); `url` absolute when `public_base_url` set (REST + list).
- `GET /documents/{id}/annotations?limit=&offset=` → `{items,total}`; `GET /me/proposals?limit=&offset=` (keeps `total`); `GET /me/proposals/count` → `{"pending": n}`.
- Search hits: `notes_total` = count of notes readable by the caller per hit (one grouped query).
- Evidence JSON: `created_by_name` (username).
- `GET /me/notifications?since_id=` (id > since_id, newest first) + response `max_id`.
- `GET /admin/tags?q=&limit=&offset=` and `GET /admin/groups?q=&limit=&offset=` → `{items,total}`; `top_uploaders` `?top=` (≤50).
- `GET /admin/settings/schema` → `SettingsIn.model_json_schema()`.
- `POST /admin/tags/merge` → `{documents: n, document_ids: [...], target}`.
- Connector views: `last_run.status = "queued"` (with `job_id`) when a queued `connector_run` job exists.
- [ ] Tests per item incl. 404/visibility: handoff documents exclude invisible docs; notes_total counts only readable notes.
- [ ] **Commit** `feat(api): web contract additions for handoffs, notes, notifications and admin`

### Task 5: Maintenance jobs — retention, blob GC, ES tag reconcile

**Files:** Create `roracle/maintenance.py`; modify `worker.py`, `api/routes/admin_ops.py`, `config.py`; docs (runbook). Test `tests/test_maintenance.py`.
**Interfaces:**
- Settings `retention_deleted_days: int = 0`, `maintenance_interval_s: int = 86400`.
- Job `maintenance` (periodic via `ensure_maintenance`): purge (R5: docs deleted > N days → delete ES chunks by doc_id, delete versions/notes per FK, decrement refcounts), blob GC (blobs with no `document_versions`/`evidence` reference, older than 1 day → S3 delete then row delete), reconcile (enqueue `acl_sync` per live document in id-cursor batches of 500 via `reconcile_tags` job — last-writer fix).
- `POST /admin/maintenance/{purge|gc|reconcile}` → 202 `{job_id}` (audited, admin only).
- [ ] Tests: purge respects age and leaves live docs; GC keeps a blob shared by a live version and a purged one; reconcile enqueues one acl_sync per doc and resumes from cursor.
- [ ] **Commit** `feat(ops): retention purge, blob gc and es tag reconcile jobs`

### Task 6: Embedding-model switch and re-embed/reindex (opus review)

**Files:** Create `roracle/reembed.py`, `api/routes/admin_index.py`; modify `search_index.py`, `retrieval.py`, `ingest/pipeline.py`, `runtime.py`, `worker.py`, `api/app.py`; docs (runbook procedure). Tests `tests/test_reembed.py` (fake ES), `tests/test_reembed_es.py` (integration).
**Interfaces:**
- Indices `chunks-<slug>-<gen>`; read alias `chunks` → exactly one (active) index; mutation alias `chunks-all` → all live generations. All `update_by_query`/`delete_by_query` (sync_acl, delete_stale_chunks, mark_previous_versions, library tags) target `chunks-all`; bulk writes and kNN target the active index for the active model (R4).
- `POST /admin/reindex {model, dims}` → 202 `{job_id}` (409 if one running); `GET /admin/reindex` → `{active:{index,model,dims}, running:{target, copied, total, phase}|null}`.
- Job `reembed` phases: `copy` (search_after over active index in batches of 200, re-embed `text` (+ summary on chunk 0) with target model, bulk to target; re-enqueue self with cursor) → `cutover` (atomic alias swap + persist active model) → `catchup` (copy ids present in old but missing in target; runs twice, 300 s apart). Old index stays in `chunks-all` until admin `DELETE /admin/reindex/old`.
- [ ] Tests: fake ES: alias swap is a single `update_aliases` call; mutations hit `chunks-all`; cutover changes the model used by retrieval/ingest; catch-up copies a chunk ingested during copy. Integration (flock, Ollama ON): reindex same model to gen 2, search still returns results.
- [ ] **Commit** `feat(search): embedding model switch with background re-embed and alias cutover`
