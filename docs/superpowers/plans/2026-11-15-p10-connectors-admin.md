# P10 Connectors, Admin API & Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admins pull documents from S3 buckets, filesystem mounts and internal websites on a schedule, run the system from an audited admin API (jobs, near-dup review, runtime settings, tags, audit log, stats, health), and users get in-app notifications about their uploads.

**Architecture:** One migration `0008_p10` adds `connectors`, `connector_runs`, `connector_items`, `notifications`, `app_settings` and `documents.connector_id`. Connector sources are small iterators (`roracle/connectors/{s3,fs,http}.py`) that yield items; a `connector_run` job stages each item and hands it to the shared intake (`ingest/intake.py`, also used by `POST /documents`), so connector items go through the same exact-dedup + ingest pipeline. A self-re-enqueuing `connector_tick` job turns schedules into `connector_run` jobs on the existing Postgres queue. Runtime settings are DB overrides merged over env `Settings` (`runtime.effective`). All `/admin/*` routes share one `require_admin` dependency that also writes the audit row.

**Tech Stack:** P7 stack. No new dependencies (boto3, httpx/httpcore, lxml, stdlib `ipaddress`, `os.fwalk`, `urllib.robotparser`).

**Spec:** `docs/spec.md` §4 (sources, connector review), §9 Admin, §10 Notifications; ADR-0003, ADR-0005, ADR-0013.

## Global Constraints

- Every `/admin/*` route depends on `require_admin` (in `api/routes/admin_deps.py`): unauthenticated → 401, non-admin (incl. every PAT — PATs are never admin) → 403 `{"detail":"admin only"}`. It writes one `audit_log` row per request: `action="admin.<endpoint function name>"`, `target=<request path>`, `detail={"query": {...}}`. Mutating routes add a second row with a body summary (secrets → `"***"`).
- Admin lists are keyset-paged: `?before_id=&limit=` (default 50, max 200) → `{"items": [...], "next_before_id": int|null}` (job/audit/run/notification ids are bigints). Users list uses `?page=&q=` (offset; small table).
- Runtime settings: DB row wins over env; PUT value `null` deletes the override. Only whitelisted keys (Task 2) are accepted; unknown key → 422.
- Notifications: `notify.emit(session, user_id, kind, payload)` and `notify.emit_admins(session, kind, payload)` add rows to the caller's transaction (no commit). Payloads never contain anything the recipient cannot see; internal job errors are shown only if they carry the queue's `user:` prefix, else `"processing failed"`. Documents with `connector_id` emit no per-item notifications.
- Connector items reuse `ingest/intake.py`; the item's documents are owned by `connector.owner_id`, carry `connector_id`, use the connector's `visibility`/`org_unit`. The run handler never raises (records a `failed` run instead) and writes run rows and items in its own short sessions (R9 spirit: progress survives a crash).
- Caps: per item `effective.max_upload_bytes`; per run `ROR_CONNECTOR_MAX_ITEMS` (1000) items and `ROR_CONNECTOR_MAX_RUN_S` (3600) seconds → run status `partial`. Run log ≤ 500 lines (keep the last ones).
- Migration file `migrations/versions/0008_p10.py`: `revision = "0008_p10"`, `down_revision = "0007"`.
- Tests: `ROR_DATABASE_URL=postgresql+psycopg://roracle:roracle@localhost:55433/roracle_p10_test` on every run; Ollama is off — fakes only; integration runs inside `flock /tmp/roracle-integ.lock`. Files < 500 lines; `uv run ruff check . && uv run ruff format --check .` clean.

## Review Focus

1. No `/admin/*` route reachable by a non-admin or a PAT; every admin request audited — Task 2, 3 (route-table test).
2. Filesystem connector cannot escape its allowlisted root (symlinked file, symlinked dir, `..`, root itself a symlink to elsewhere) — Task 4.
3. HTTP crawl: only http/https; every connect (incl. redirects and DNS rebinding) goes to a vetted IP; private/loopback/reserved blocked unless inside an allowlisted CIDR; link-local (metadata) blocked always; size/time caps hold — Task 5.
4. Notification payloads and admin review never leak invisible docs to non-admins; `/me/notifications` only returns own rows — Task 1.
5. Runtime overrides actually take effect (upload limit, PAT toggle incl. MCP, chat models, near-dup threshold in the worker) — Task 2.

## Rulings (plan-level)

- R1: "Connector ingests go to an admin review queue" = connector versions that hit the near-dup check park in `review` and appear in `GET /admin/review` (alongside user uploads awaiting a decision); admins decide with the existing near-dup endpoint (owner-or-admin). Non-dup connector items become searchable directly — cost if wrong: add a per-connector `require_approval` gate later.
- R2: connector documents are owned by the admin who created the connector (`owner_id`), transferable later; `visibility` is required on create (no implicit default), `shared` unsupported for connectors — cost: one owner sees connector near-dup prompts.
- R3: schedule = interval minutes (`schedule_minutes` ≥ 5 or null = manual), not cron — no new dependency — cost: no wall-clock schedules.
- R4: S3 credentials are stored in `connectors.config` and are write-only through the API (`"***"` on read; PATCH with `"***"` keeps the old value). S3 endpoints are admin-chosen and not SSRF-filtered — cost: secrets in Postgres (DB is already the system of record; P11 may move to k8s secrets).
- R5: robots.txt honoured by default for HTTP connectors (`respect_robots`, fetched through the same guarded client); crawl is same-origin as each seed only.
- R6: admin endpoints return 403 (not 404) to non-admins: route existence is not secret; 404 rule stays for documents.
- R7: users/groups come from the local `users` mirror (as of each user's last login), not the Keycloak admin API — no admin credentials needed — cost: never-logged-in users invisible.
- R8: quotas: `upload_quota_bytes` (per-user sum of live version sizes; 0 = unlimited) enforced on `POST /documents` (413); `rate_limit_per_minute` stored only, enforced at ingress in P11 (needs a shared counter).
- R9: embedding-model switch + re-embed, retention/purge, writing profiles → P11/P9 (not trivial).
- R10: link-local (169.254.0.0/16, fe80::/10) is blocked even if inside an allowlisted CIDR (cloud metadata) — cost: none realistic.
- R11: one audit row per admin request (incl. reads) from the dependency — simple, complete — cost: audit volume from admin UI polling (admin UI should not poll faster than 30 s).

### Task 1: Schema + notifications (sonnet review)

**Files:** `migrations/versions/0008_p10.py`, `roracle/models.py`, `roracle/notify.py`, `roracle/queue.py` (failure hook), `roracle/ingest/pipeline.py` (`_dedupe`), `roracle/ingest/state.py` (`mark_searchable_and_enqueue`), `api/routes/notifications.py`, `api/app.py`; Test `tests/test_notifications.py`, `tests/test_p10_schema.py`.
**Interfaces:**
- Tables: `connectors(id uuid pk, name text unique, kind text check in ('s3','fs','http'), config jsonb, owner_id fk users, visibility text check in ('private','org_unit','everyone'), org_unit text default '', schedule_minutes int null, enabled bool default true, next_run_at tz null, last_run_at tz null, created_at)`; `connector_runs(id bigserial, connector_id fk cascade, status check in ('running','ok','partial','failed'), started_at, finished_at null, stats jsonb default {}, log text default '')` index (connector_id, id); `connector_items(connector_id fk cascade, key text, etag text default '', sha256 text, document_id fk documents on delete set null, seen_at; pk(connector_id,key))`; `notifications(id bigserial, user_id fk users cascade, kind text, payload jsonb, created_at, read_at null)` index (user_id, id); `app_settings(key text pk, value jsonb, updated_at, updated_by uuid null)`; `documents.connector_id uuid null fk connectors on delete set null` + index.
- `notify.emit(session, user_id, kind, payload) -> None`, `notify.emit_admins(session, kind, payload)`, `notify.on_job_failed(session, job)`: `ingest` → owner gets `upload.failed {document_id, version_id, filename, error}` (skip connector docs); other kinds → admins get `ops.job_failed {job_id, kind, error}`. Called from `queue.run_once` after `fail()` when the job ended `failed`, inside its own SAVEPOINT; an emitter exception is logged, never breaks the queue.
- Emitters: `upload.searchable {document_id, version_id, filename, title}` in `mark_searchable_and_enqueue` (same short transaction); `upload.near_dup {document_id, version_id, filename, match_document_id, match_title}` in `_dedupe` when parked (handler session; not a documents write).
- `GET /me/notifications?unread_only=false&before_id=&limit=50` → `{items:[{id,kind,payload,created_at,read_at}], next_before_id, unread}`; `POST /me/notifications/read {ids?: [int], all?: bool}` → `{updated: n}` (other users' ids silently ignored).
- [ ] Tests: migration up/down; searchable/near-dup/failed emitted once with the right payload; failed non-`user:` error → `"processing failed"`; connector doc → no notification; non-ingest permanent failure → every admin gets `ops.job_failed`; paging + unread count; marking another user's id updates 0.
- [ ] **Commit** `feat(notify): notifications table, emitters and inbox API`

### Task 2: Admin guard + runtime settings (opus review)

**Files:** `api/routes/admin_deps.py` (`require_admin`, `Admin = Annotated[Principal, Depends(require_admin)]`, `page_params`), `api/routes/admin.py` (switch existing routes to `require_admin`), `api/routes/admin_settings.py`, `roracle/runtime.py`, `roracle/config.py`, `roracle/auth.py` (`get_app_settings` merges overrides), `roracle/mcp/auth.py` (`dep()` gives `get_app_settings` a session), `roracle/ingest/pipeline.py` (`_dedupe` uses `runtime.effective`), `roracle/retrieval.py` (`RERANK_TOP` → `settings.rerank_depth`), `api/routes/documents.py` (quota), `docs/enterprise/deployment-guide.md`; Test `tests/test_admin_settings.py`.
**Interfaces:**
- `runtime.KEYS`: `chat_models: list[str]` (1-20 non-empty), `chat_default_model: str` (must be in effective `chat_models`; effective list puts it first), `chat_passages: int 4-12`, `rerank_depth: int 10-200`, `near_dup_threshold: float 0.5-1`, `near_dup_summary_threshold: float 0.5-1`, `max_upload_bytes: int 1 MiB-5 GiB`, `upload_quota_bytes: int ≥ 0`, `rate_limit_per_minute: int ≥ 0`, `pats_enabled: bool`. Validated with one pydantic model.
- `runtime.effective(session, base: Settings) -> Settings` (`base.model_copy(update=...)`; `chat_models` stored as list, mapped to the comma string). `runtime.overrides(session) -> dict`. New `Settings` fields: `rerank_depth=50`, `upload_quota_bytes=0`, `rate_limit_per_minute=0`.
- `get_app_settings(request, session=Depends(get_session))` returns `effective(...)`; MCP `dep(app, get_app_settings)` uses `open_session(app)`.
- `GET /admin/settings` → `{key: {value, source: "env"|"override"}}`; `PUT /admin/settings {key: value|null, ...}` → same shape; audited with old/new values.
- Upload quota: `sum(DocumentVersion.size)` of the user's non-deleted documents + new size > quota → 413 `"upload quota exceeded"`.
- [ ] Tests: non-admin JWT and admin-user PAT → 403 on GET/PUT settings, no token → 401; every admin call writes an `admin.*` audit row; override `max_upload_bytes` → upload 413; `pats_enabled=false` override → PAT 401 on REST and `/mcp`; `chat_models` + default reflected in `/chats/models`; null resets; bad value/unknown key → 422; worker `_dedupe` honours overridden threshold; quota 413.
- [ ] **Commit** `feat(admin): admin guard and runtime settings`

### Task 3: Admin dashboards (sonnet review)

**Files:** `api/routes/admin.py` (users, groups, review, tags, audit), `api/routes/admin_ops.py` (jobs, retry, stats, health), `api/app.py`, `docs/enterprise/operations-runbook.md`; Test `tests/test_admin_api.py`.
**Interfaces:**
- `GET /admin/users?q=&page=1` → `{items:[{id,username,email,groups,is_admin,created_at,last_login_at,documents,bytes}], total}`; `GET /admin/groups` → `[{group, members}]`.
- `GET /admin/jobs?status=&kind=&before_id=&limit=` → items `{id,kind,status,attempts,last_error,payload,created_at,updated_at,document_id?,filename?}`; `GET /admin/jobs/summary` → `{jobs:{kind:{status:n}}, versions:{stage:n}}`; `POST /admin/jobs/{id}/retry` (failed only, else 409; `status=queued, attempts=0, run_after=now(), last_error=null`; ingest → version `ingest_attempts=0`) → job; `POST /admin/jobs/retry-failed {kind?}` → `{retried: n}`.
- `GET /admin/review?source=all|upload|connector&before_id=` (versions in stage `review`; keyset on created_at,id is fine as offset `page` — implementer's choice, document it) → `{items:[{document_id,version_id,filename,owner,connector_id,uploaded_at,match:{document_id,title}|null}]}`.
- `GET /admin/tags` → `[{tag, documents}]`; `POST /admin/tags/merge {sources:[str], target:str}` (same tag validation as library) → `{documents: n}`; enqueues `acl_sync` per affected doc.
- `GET /admin/audit?action=<prefix>&user_id=&before_id=&limit=` → items `{id,at,user_id,username,action,target,client,detail}`.
- `GET /admin/stats` → `{documents:{live,deleted}, versions:{stage:n}, blobs:{count,bytes}, users, chats, connectors, top_uploaders:[{user_id,username,documents,bytes}]}` (top 10).
- `GET /admin/health` → `{status:"ok"|"degraded", checks:{db,elastic,storage}: "ok"|"error: <type>", queue:{queued,running,failed,oldest_queued_s}, scheduler:{tick_queued: bool}}` (uses `get_es`, `get_blob_store`; each check catches its own errors).
- [ ] Tests: route-table test iterates `app.routes` with path starting `/admin` and asserts each declares `require_admin` (dependant tree) and returns 403 for a non-admin; retry resets job + R17; tag merge with a doc already holding the target; audit filters + paging; stats/health shapes with fake ES/store.
- [ ] **Commit** `feat(admin): users, jobs, review, tags, audit, stats and health API`

### Task 4: Connector framework, S3 + filesystem (opus review)

**Files:** `roracle/ingest/intake.py` (`add_document`, `add_version`), `api/routes/documents.py` (use intake), `roracle/connectors/__init__.py` (`Item`, `source_for(connector, settings)`), `roracle/connectors/s3.py`, `roracle/connectors/fs.py`, `roracle/connectors/run.py` (`handle_connector_run`, `handle_connector_tick`, `ensure_tick`), `roracle/worker.py`, `roracle/config.py` (`connector_fs_roots`, `connector_max_items`, `connector_max_run_s`, `connector_tick_s=60`), `api/routes/admin_connectors.py`, `api/app.py`, `docs/enterprise/{deployment-guide,security}.md`; Test `tests/test_connectors.py`, `tests/test_connector_fs.py`.
**Interfaces:**
- `Item(key, etag, filename, content_type, size|None, open: Callable[[], BinaryIO])` — `open()` valid only until the iterator advances.
- `intake.add_document(session, store, staged, *, owner_id, org_unit, visibility, filename, content_type, title=None, connector_id=None) -> (Document, DocumentVersion)` and `add_version(session, store, staged, doc, *, filename, content_type) -> DocumentVersion`: put blob, upsert `blobs` refcount, rows, enqueue `ingest`. `POST /documents` behaviour unchanged.
- S3 config `{endpoint, bucket, prefix="", region="us-east-1", access_key?, secret_key?}` (no keys → unsigned). FS config `{path, include=["*"]}`: `realpath(path)` must lie in a `realpath` of `ROR_CONNECTOR_FS_ROOTS` (empty = fs connectors rejected with 422); walk with `os.fwalk(follow_symlinks=False)`, open with `O_NOFOLLOW` + `dir_fd`, regular files only, skip dot-files, size from `fstat`; key = relative path, etag = `"{mtime_ns}:{size}"`.
- Run per item (own short session each): unchanged etag → skip; stage (cap) → same sha as recorded → skip; sha already in a live document → `duplicate` (record item → that doc); item mapped to a live connector doc → `add_version`; else `add_document`. Stats `{seen, ingested, versions, unchanged, duplicate, too_large, errors}`. Source/listing exception → run `failed`. Status `partial` on errors or caps. Non-ok run → `emit_admins("ops.connector_run", {connector_id,name,run_id,status,stats})`.
- Tick: due enabled connectors (`FOR UPDATE SKIP LOCKED`) with no queued/running `connector_run` job → enqueue; `next_run_at = now + schedule`; re-enqueue the tick (`run_after=connector_tick_s`) unless another tick is queued. `ensure_tick(sf)` at worker start.
- Routes: `GET/POST /admin/connectors`, `GET/PATCH/DELETE /admin/connectors/{id}`, `POST /admin/connectors/{id}/run` (202 `{job_id}`; 409 if queued/running), `GET /admin/connectors/{id}/runs?before_id=`, `GET /admin/connectors/{id}/runs/{run_id}` (with log). Kind-specific config validated by pydantic; `secret_key` returned as `"***"`.
- [ ] Tests: upload regression suite still green; fs: symlinked file, symlinked dir, path outside roots, root-is-symlink-to-outside → no escape; S3 against the MinIO test bucket (prefix, unchanged re-run = 0 ingested, changed object → new version); duplicate bytes skipped; caps → `partial`; tick enqueues once per due connector; run of a deleted connector is a no-op; secrets redacted; non-admin 403.
- [ ] **Commit** `feat(connectors): connector runs with S3 and filesystem sources`

### Task 5: HTTP crawl connector (opus review)

**Files:** `roracle/connectors/netguard.py`, `roracle/connectors/http.py`, `roracle/connectors/__init__.py`, `roracle/config.py` (`connector_http_allow_cidrs`, `connector_http_timeout_s=60`), `docs/enterprise/{deployment-guide,security}.md`; Test `tests/test_connector_http.py`.
**Interfaces:**
- `netguard.check_ip(ip, allow_cidrs) -> None|raise Blocked`: unwrap IPv4-mapped; link-local always blocked (R10); `not ip.is_global` (private, loopback, reserved, CGNAT, ULA, unspecified, multicast) blocked unless inside an allowlisted CIDR.
- `netguard.GuardedBackend(httpcore.SyncBackend)`: `connect_tcp` resolves with `getaddrinfo`, checks every address, connects to the first vetted IP literal (TLS SNI/Host still the URL host). `netguard.client(settings) -> httpcore.ConnectionPool(network_backend=GuardedBackend, ...)` — no env proxies.
- `netguard.fetch(pool, url, max_bytes, deadline_s) -> Fetched(url, status, content_type, etag, path)`: scheme ∈ {http, https} (else Blocked); manual redirects ≤ 5 (each re-checked); streams to a temp file; aborts over `max_bytes` (Content-Length or streamed) or deadline → `TooLarge`/`TimeoutError`.
- HTTP config `{start_urls (1-20), max_depth=1 (0-3), max_pages=100 (1-1000), respect_robots=true, ingest_html=false}`. BFS per seed, same origin only; links from `<a href>` via `lxml.html` (HTML parsed only if ≤ 5 MiB); items = responses with document content types (pdf, ms office/openxml, opendocument, text/plain, text/markdown; html when `ingest_html`); key = final URL, etag = ETag or Last-Modified.
- [ ] Tests (local `http.server` on 127.0.0.1 in a thread): loopback blocked without allowlist, allowed with `127.0.0.0/8`; `169.254.169.254` blocked even when `169.254.0.0/16` allowlisted; IPv4-mapped `::ffff:10.0.0.1` blocked; `file:`/`ftp:` rejected; redirect to a blocked host blocked; DNS rebinding (patched `getaddrinfo` returns public then private) blocked at connect; oversize body aborted; robots disallow honoured; depth/page caps; off-origin links not followed.
- [ ] **Commit** `feat(connectors): SSRF-safe HTTP crawl source`
