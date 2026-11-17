# rOracle Operations Runbook

Audience: on-call operators. Commands assume namespace `roracle`, Helm release `roracle`,
CNPG cluster `roracle-pg`, ECK cluster `roracle` (service `roracle-es-http`). Adjust to your names.

## 1. Health at a glance

| Signal | Where | Healthy |
|---|---|---|
| API liveness | `GET /healthz` | `{"status":"ok","version":"x.y.z"}` |
| API readiness | `GET /readyz` | 200; on 503 the body names the failing check (`db`, `es` or `s3`) |
| Worker | `GET :8001/healthz` | 200; 503 = job loop stalled > 5 min |
| Queue depth | `roracle_jobs{kind,status}` (or `SELECT kind, status, count(*) FROM jobs GROUP BY 1,2;`) | `queued` drains; few `failed` |
| Oldest queued job | `roracle_jobs_oldest_queued_seconds{kind}` | < 5 min |
| Job outcomes | `rate(roracle_job_runs_total{outcome=~"retry|failed"}[5m])` (worker) | near 0 |
| Ingest failures | `roracle_ingest_stage_failures_total{stage}` | near 0 |
| API traffic | `roracle_http_requests_total{method,route,status}`, `roracle_http_request_duration_seconds` (route = matched template; for SSE/streaming responses the duration is time-to-headers only) | 5xx < 1% |
| Search latency | Grafana: `roracle_search_seconds` p95 | < 500 ms |
| Chat TTFT | `roracle_chat_ttft_seconds` p95 | < 2 s |
| Ingest latency | `roracle_ingest_seconds` p95 (upload → searchable) | < 120 s |
| Reranker (when enabled) | `rate(roracle_rerank_fallback_total{reason}[5m])`, `roracle_rerank_seconds` p95 | fallbacks near 0; p95 < 800 ms for 200 passages |
| Near-duplicate lookups | `roracle_neardup_exhaustive_total{reason}` | near 0 (a rise means the LSH backfill is missing or failed: the slow exhaustive scan runs) |
| Elastic | `GET _cluster/health` | green |
| Postgres | `kubectl cnpg status roracle-pg` | primary + replicas streaming |

Metrics come from `/metrics` on the api (also the DB-backed `roracle_jobs*` gauges, collected at scrape time with a 2 s statement timeout; with several api replicas aggregate with `max by`) and on the worker (`roracle_job_runs_total`, `roracle_ingest_stage_failures_total`). Both endpoints are cluster-internal only.

### Suggested alerts

Queue oldest job > 15 min; failed jobs > 50/h; search p95 > 1 s for 10 min; TTFT p95 > 4 s;
Elastic yellow > 15 min / red immediately; Postgres replication lag > 60 s; MinIO drive offline;
ClamAV signatures older than 3 days; 5xx rate > 1%; PAT use bursts; rerank fallbacks > 5% of requests for 10 min.

## 2. Routine procedures

### Scale workers for a backlog
```bash
kubectl -n roracle scale deploy/roracle-worker --replicas=8
```
Workers claim jobs with `FOR UPDATE SKIP LOCKED`; adding replicas is always safe.

### Retry failed jobs
```sql
UPDATE jobs SET status='queued', attempts=0, run_after=now(), last_error=NULL
WHERE status='failed' AND kind='ingest' AND updated_at > now() - interval '1 day';
```
(The admin ingest dashboard does the same per job.)

### Evidence scans (`evidence_scan` job)
Each evidence upload queues a `jobs` row `kind='evidence_scan'`; the worker streams the blob to ClamAV and
sets `evidence.scan_status`: `pending` (queued), `clean`, `infected` (download blocked), `failed`
(blob missing or scanner rejected it, e.g. over `StreamMaxLength`) or `unscanned` (no `ROR_CLAMAV_HOST`).
Scanner outages retry like ingest jobs. Re-run a failed or stuck scan:
```sql
UPDATE jobs SET status='queued', attempts=0, run_after=now(), last_error=NULL
WHERE kind='evidence_scan' AND status='failed';
```
For `failed` rows caused by size, raise ClamAV limits (see deployment guide) and re-queue.

### Change the embedding model (zero downtime)
Indices are `chunks-<model-slug>-<gen>`. Read alias `chunks` points at exactly one (the active) index; mutation
alias `chunks-all` points at every live generation, and every ACL/tag/delete/`is_latest` write goes through it, so
changes made during a re-embed reach both generations. Search and ingest use the active model persisted by cutover
(`app_settings` keys `embed_model`, `embed_dims`, `embed_index`); `ROR_EMBED_MODEL/_DIMS` is only the bootstrap default.
1. Make sure the new model is served by the embedding endpoint and ES has room for a second copy of the vectors.
2. `POST /admin/reindex {"model": "...", "dims": N}` -> `202 {job_id}` (409 if one is running). Same model is allowed
   (builds the next generation).
3. Follow `GET /admin/reindex` (`running.phase`: `copy` -> `cutover` -> `catchup`, `copied`/`total`). Search keeps
   using the old index until cutover; ingest during the copy keeps writing to the old index.
4. Cutover: slow cleanup first, then (under an advisory lock) persist the active model and do one atomic alias swap as the last ES step. `ensure_index` (ingest, worker start) moves `chunks` only to the Postgres-active index, so a stale worker never pulls it back; catch-up fails loudly if `chunks` drifts. It also nulls `document_versions.summary_embedding`
   when the model or dims changed (derived cache; re-created on re-ingest; near-duplicate summary matching is skipped
   for those versions meanwhile) and queues `reconcile_tags` (ACL/tags of every live document re-pushed from Postgres).
5. Two catch-up passes (300 s apart) copy chunks that reached the old index meanwhile. Ingests that straddle the
   cutover fail once and are retried with the new model (ingest always runs on the persisted active model/index).
   Known, accepted: summary vectors are cleared just before the cutover commit, so an old-settings ingest landing in
   that window can persist one stale same-dims summary vector; it only affects near-duplicate matching until that
   document is re-ingested.
   Copies are made safe against concurrent change: ACL, tags and title of every copied chunk are re-read from
   Postgres after the ES read (never copied from the old index); chunks of deleted documents, purged versions or
   shrunk re-ingests are skipped or removed from the new index after the write; `is_latest` is recomputed from
   Postgres. `reconcile_tags` is queued at the end of the copy, at cutover and after each catch-up pass.
   Known, accepted: readers that go through the `chunks` read alias (MCP, note anchoring, passage read) do not see
   chunks ingested during the copy until the first catch-up pass.
6. After verifying search, free the space: `DELETE /admin/reindex/old` (204; 409 while running, or 409 when the
   `chunks` alias does not point at exactly the active index). Until then the old generation stays in `chunks-all`
   and keeps receiving mutations. It drops every generation except the active one (per Postgres).
Failure: a failed `reembed` job (see `GET /admin/jobs?kind=reembed`) can be retried; copy is idempotent. To abandon,
`DELETE /admin/reindex/old` drops the half-built generation while the alias still matches the active index. If a
cutover failed permanently after the alias swap (`GET /admin/reindex` shows `old` containing the index that `chunks`
points at), the delete is refused with 409: re-run the failed job (idempotent) or point `chunks` back at the active
index before abandoning.
Upgrade note: the worker creates `chunks-all` and adopts a legacy concrete index named `chunks` (reindex +
atomic alias swap) at start. Roll out workers first; until a worker has started, ACL/tag updates find no `chunks-all`
and are no-ops, healed by the next `reconcile` maintenance run (run it after the upgrade).
Capacity: plan for double vector storage during the re-embed.

### Re-embed after embedding prompt/model change (pre-production only)
P2b put the document summary into chunk embedding prompts. Vectors indexed earlier are stale but
still functional, so this is optional. Downtime procedure:
1. Stop workers.
2. Drop the index: `DELETE /chunks-<slug>-1` (slug = lowercased model name, non-alphanumerics to `-`).
3. In Postgres (one statement; resets attempts, skips soft-deleted documents, enqueues only the reset rows):
   `WITH r AS (UPDATE document_versions dv SET stage='deduped', ingest_attempts=0, summary_embedding=NULL FROM documents d WHERE d.id=dv.document_id AND d.deleted_at IS NULL AND dv.stage='searchable' RETURNING dv.id) INSERT INTO jobs (kind, payload) SELECT 'ingest', jsonb_build_object('version_id', id::text) FROM r;`
4. Start workers.
`summary_embedding=NULL` is required: the stored summary vector is not model-stamped, so clearing it makes
`_index` lazily re-embed it with the new model. Near-duplicate detection compares summary vectors, which is
only meaningful within one embedding model.
Until each version is searchable again it is absent from search (non-owners lose visibility
first-hand; owners only via the document list). Do not use on a production corpus.

### Delete or transfer a document
`DELETE /documents/{id}` (owner or admin, `204`) soft-deletes: PG hides it at once, ES chunks are removed best effort
(the retention purge below finishes the job), an `acl_sync` is queued and the owner is notified when an admin deletes.
`POST /documents/{id}/transfer {"user_id": ...}` (owner or admin) changes the owner and re-syncs ES ACLs; both are
audited (`document.delete`, `document.transfer`). Drafts use `/drafts` instead (`409`).

### Maintenance: retention purge, blob GC, tag reconcile (`maintenance` job)
A self-rescheduling `maintenance` job (every `ROR_MAINTENANCE_INTERVAL_S`, default daily; created at worker start)
runs three steps; trigger one on demand with `POST /admin/maintenance/{purge|gc|reconcile}` (admin JWT, audited,
`202 {job_id}`; follow it in `GET /admin/jobs?kind=maintenance`).
- **purge**: hard-deletes documents soft-deleted more than `ROR_RETENTION_DELETED_DAYS` days ago (ES chunks by
  `doc_id`, versions, notes/evidence rows including relation notes from other documents targeting it, shares) in batches of 100 (a full batch queues a follow-up job).
  `0` (default) = off: the endpoint then does nothing. Also deletes the version's `derived/<version_id>/` objects
  (docling JSON, rendition). Irreversible; restore from backup only. On a versioned bucket, noncurrent versions
  of purged `blobs/` and `derived/` objects remain until a noncurrent-expiry rule removes them (the chart
  sets one only for `tmp/`; add `mc ilm rule add --prefix blobs/ --noncurrent-expire-days N` for real erasure). A document
  whose purge fails (e.g. ES error) is logged and skipped; the rest of the batch proceeds.
  If the periodic job ever ends `failed`, restart a worker to recreate it.
- **gc**: deletes `blobs/` objects and `blobs` rows that no document version or evidence row references and
  that are older than 1 day. Uploads lock the `blobs` row before copying the object and GC skips
  locked rows (`SKIP LOCKED`), re-checks references under the lock and spares objects written in the last
  day, so an upload of the same content is not left pointing at a deleted object. `tmp/` is never touched
  (in-flight uploads; see "Clean up `tmp/`"). A blob shared with a live version is always kept.
- **reconcile**: walks the live documents in batches of 500 (`reconcile_tags` job chain), compares each document's Postgres
  `acl_hash` (ACL, title, tags) with the hashes in ES and queues an `acl_sync` ONLY for documents that differ (deduplicated
  against already queued ones); an in-sync corpus costs no ES writes. Use after an ES restore or suspected tag drift.

### Rebuild the search index from scratch
Elastic holds no unique data. `POST /admin/reindex {"model": "<current model>", "dims": N}` (admin JWT, audited, `202 {job_id}`;
`GET /admin/reindex` shows progress) rebuilds into the next index generation; with the same model it is a plain rebuild.
Search stays on the old generation until the cutover swaps the read alias.

### Clean up `tmp/` upload leftovers

API uploads stage in the blob bucket under `tmp/<uuid>` and are deleted after the copy to `blobs/`. A crash
mid-upload leaves an object or an incomplete multipart upload behind. The bucket lifecycle rule (`tmp/`, 1 day)
removes objects; check it with `mc ilm rule ls <alias>/<bucket>` and add it if missing (`mc ilm rule add --prefix
tmp/ --expire-days 1 <alias>/<bucket>`). Incomplete multipart uploads: MinIO discards stale ones after 24 h;
on AWS S3 add an AbortIncompleteMultipartUpload rule. Manual: `mc ls --incomplete <alias>/<bucket>/tmp/` and
`mc rm --incomplete --recursive --force <alias>/<bucket>/tmp/`. Never touch `blobs/`.

### Rotate secrets
- DB / S3 / Elastic credentials: update secret → `kubectl rollout restart deploy/roracle-api deploy/roracle-worker`.
- Keycloak signing keys: rotate in Keycloak; rOracle refetches JWKS on unknown `kid` automatically.
- PAT pepper: rotating invalidates all PATs — announce first.

### Sensitivity labels

Background: [security, section 13](security.md#13-sensitivity-labels).

- **Admin.** Web app, Admin, **Labels** (API `GET/POST /admin/labels`, `PATCH/DELETE /admin/labels/{id}`,
  `PUT /admin/labels/order {ids}` with every label id, least sensitive first). Max 20 labels; a gate must
  be on for every label ranked above one that has it (422 otherwise). Exactly one default (setting a new
  one unsets the old one). A label in use or the default cannot be deleted (409). Reordering or editing
  gates needs no reindex and takes effect on the next request. Each admin change is audited
  (`label.create|update|delete|reorder`).
- **Rename the seeded `General`** label rather than deleting it; it is the default and holds every
  pre-existing document.
- **Connector default.** A connector may name a label; otherwise its documents get the global default.
- **Export-blocked and inline-view audit queries** (`audit_log.detail` is JSONB):

```sql
-- blocked download attempts, last 7 days
SELECT at, user_id, target AS target_id, detail->>'route' AS route
FROM audit_log WHERE action = 'label.export_blocked' AND at > now() - interval '7 days' ORDER BY at DESC;
-- inline reads of block_export documents (one row per user, document and UTC day)
SELECT at, user_id, target AS document_id, detail->>'route' AS route
FROM audit_log WHERE action = 'label.view' ORDER BY at DESC LIMIT 200;
-- label changes for one document, then for its notes (annotation.label targets the note id)
SELECT at, user_id, detail FROM audit_log WHERE action = 'document.label' AND target = '<doc id>' ORDER BY at;
SELECT at, user_id, detail FROM audit_log WHERE action = 'annotation.label'
  AND target IN (SELECT id::text FROM annotations WHERE document_id = '<doc id>') ORDER BY at;
```

  `target_id` is the document id, except `chat.export` rows where it is the chat id.

  `label.*` events are reachable via `GET /admin/audit?action=label.` (a prefix match); `document.label` and
  `annotation.label` via `action=document.label` and `action=annotation.label`. For `document.label`, `from`/`to`
  are label ids.
- **Elasticsearch drift.** After a label change `acl_sync` converges the index; a stuck job shows as a
  failed `acl_sync` in `GET /admin/jobs?status=failed&kind=acl_sync` (retry with `retry-failed`). Until it
  converges the Postgres post-filter enforces the new label.

### Evaluate retrieval quality (eval CLI, reference set, trend)
Everything here is admin-only and reads private data through the production path per principal (see `docs/eval/README.md`).
- **CLI** (a one-off pod/Job with the app env; counts against `postgres.reservedConnections`): `roracle eval run --set <name> [--path chat|search] [--judge]`
  scores a stored set; `roracle eval compare --set <name> --baseline "k=v" --candidate "k=v" [--scope all|fact|multihop|global] --out docs/eval/<file>.md`
  writes the gate report (PASS / FAIL / INSUFFICIENT); `roracle eval import-beir <dir> --set scifact --owner <non-admin user>` seeds the offline
  SciFact baseline; `roracle eval ci` is the CI fixture job. Exit code 1/2 and a message name refused input (a set with only admin principals).
- **UI/API**: Admin -> Eval (`/admin/eval/*`): create sets, add or import queries, generate draft questions from the corpus
  (`POST /admin/eval/sets/{id}/generate` -> `eval_generate` job; an admin reviews the drafts), run a set (`POST /admin/eval/runs` -> `eval_run`
  job on the worker), read the trend (`GET /admin/eval/runs?set_id=`). One set is the **reference set** (`PATCH /admin/eval/sets/{id} {"is_reference": true}`).
  Curate it to >= 200 active queries with >= 3 principals (>= 1 non-admin): below that every gate verdict is INSUFFICIENT.
- **Automatic runs**: after a re-embed cutover's catch-up and after a rebuild into the active index an `eval_run` is queued on the
  reference set (`ROR_EVAL_ON_REINDEX=true`); results land in the trend. A run with any ACL violation is `failed`: treat it as a
  security finding.
- **Reading the trend**: compare `p_ndcg@10`, `p_recall@10/100` and the judge keys (`faithfulness`, `citation_support`) between runs of the same set;
  a drop after a model change or rebuild is the signal to roll back (re-embed again with the previous model). Latency p50/p95 per run is stored too.
- **Reranker**: `roracle_rerank_fallback_total{reason}` counts requests that kept the fused order (`reason` = `timeout`, `http` or `malformed`); the reranker is off until its
  gate passes, so a non-zero counter with `ROR_RERANK_URL` empty is a bug.

### Rebuild derived index fields and shadow indices
Context notes and RAPTOR units are index-time. `roracle rebuild --what context[,raptor] [--into chunks-<slug>-shadow]` (or
`POST /admin/retrieval/rebuild {"what": [...], "into": "..."}`) enqueues `rebuild_units` jobs of 25 latest searchable, non-deleted versions each, plus a
`rebuild_watch` job that queues the eval run when the batch is done (not for a shadow). `rebuild_units` is claimed after every other job kind, so
a backfill never delays ingest. It is refused (exit 2 / `409`) while a re-embed runs, and a re-embed is refused while rebuild jobs are pending.
- **Shadow workflow** (judge a feature before it touches search): `roracle rebuild --what context --into chunks-<model-slug>-shadow` creates the shadow index
  (it joins `chunks-all`, so ACL syncs and deletes reach it, but never the read alias `chunks`); `roracle eval compare ... --candidate-index
  chunks-<model-slug>-shadow` judges it; on a PASS set `ROR_CONTEXT_NOTES=true` (and `ROR_CONTEXT_MODEL`) and rebuild into the ACTIVE index (omit
  `--into`); finally `roracle rebuild --drop chunks-<model-slug>-shadow` (refuses the active index and re-embed generations).
- Requires the notes model for `context` (`ROR_CONTEXT_MODEL`, else the job fails fast). Each version is idempotent; a broken version is skipped and the
  job fails at the end naming the count, the rest are done. Progress: `SELECT status, count(*) FROM jobs WHERE kind='rebuild_units' GROUP BY 1;`.

### Re-embed at scale (slices, failure, convergence)
`POST /admin/reindex` now enqueues `ROR_REEMBED_SLICES` (default 4) independent copy chains over disjoint `chunk_id` ranges; each page is 1,000 chunks;
`ROR_EMBED_CONCURRENCY` embedding requests are in flight per slice (size `slices x concurrency` to the embedding replicas; batch embedding calls retry
429/5xx with backoff and a shared pause). The target index is created with a 30 s refresh interval and the configured replicas.
- `GET /admin/reindex`: `running` = `{target, copied, total, phase, slices}` (progress is the sum over slices; `phase` of the newest slice job),
  **`failed`** = `{target, slices: [{i, error}]}` when a slice failed permanently (and nothing is running), `old` = leftover generations.
- **A failed slice refuses the cutover** (the read alias never moves onto an incomplete index; the last finishing slice and the cutover job both check).
  Fix the cause (embedding endpoint, ES), then `DELETE /admin/reindex/old` to drop the half-built generation and start again with `POST /admin/reindex`.
- **Cutover** order: restore the target's refresh interval and replicas, refresh it, then a convergence pass against Postgres (ACL/tag/title drift
  of every live document through `acl_hash`; chunks of every soft-deleted document removed; superseded versions demoted; orphan documents
  purged), then the alias swap, then `reconcile_tags` and two catch-up rounds per slice (300 s apart). The pass reads Postgres for every live document:
  expect minutes per million documents before the swap, with no ES writes when everything is in sync.

### Job leases, drain and worker shutdown
Jobs run under a **lease** (`ROR_JOB_LEASE_S`, 90 s, renewed every lease/3 by the worker): a crashed or stuck worker's job is requeued after the lease
(plus up to 30 s sweep), spending an attempt; a worker that lost its lease discards its result. On SIGTERM a running job gets `ROR_JOB_DRAIN_S` (chart 120 s),
then is **released** back to the queue without spending an attempt (stages and batches resume where they stopped); keep
`worker.terminationGracePeriodSeconds` >= drain + 30 (chart 150). A job that defers itself more than 200 times (for example a busy per-owner lock) fails instead of looping.
- Stuck job triage: `SELECT id, kind, status, attempts, released, lease_until, last_error FROM jobs WHERE status='running';` (an expired `lease_until` is
  swept at the next idle worker loop).
- Rolling upgrade across migrations 0014-0016: run `roracle migrate` (0014 builds queue indexes with `CREATE INDEX CONCURRENTLY`, which waits for open transactions:
  drain workers or let long jobs finish first), then roll workers with the drain; do not run old and new workers side by side longer than the drain
  (an old worker's completion is not fenced by the new claim token).
- Postgres settings these numbers assume: `jit = off`, `shared_buffers` ~ 25% of memory (`capacity-planning.md`); connection budget formula in `docs/perf/connection-budget.md`.

### Performance gate and budgets
Budgets (`docs/perf/budgets.yaml`) and the measured state (`docs/perf/report-2026-11-17.md`). The nightly GitLab job `perf` runs `roracle perf suite --profile ci`
(a small seeded scratch database `roracle_perf` + scratch `perf-*` index, a real API process, fake models with fixed latency, budgets x1.5, one retry). Run
the full profile on a staging copy only: `docs/perf/README.md` (the CLI refuses databases not matching `roracle_perf[a-z0-9_]*`; `--es-url` is not guarded,
never point it at production). A failing row names the budget; first check `jit`, Postgres `shared_buffers` and API replica count (one API process sustains about
10 searches/s, 5 filtered).

### Router, global answers and agentic retrieval (when enabled)
Off by default (`ROR_ROUTER`, `ROR_AGENTIC_RETRIEVAL`, admin runtime overrides). When on: busy admission falls back to the plain path (SSE `meta` says
`global: {skipped: busy}` or `agentic: false`), so a burst degrades answer quality, not availability. An agentic loop holds up to `ROR_AGENTIC_MAX_PARALLEL` extra
pooled DB connections per API process; watch pool waits (`ROR_DB_POOL_TIMEOUT_S`) before raising it. The planner needs a small fast model
(`ROR_AGENTIC_PLANNER_MODEL`); with a slow one calls hit `ROR_AGENTIC_CALL_TIMEOUT_S` (1.5 s) and the loop silently stops.

### Release upgrade
1. Read release notes. 2. Check a recent CNPG backup (`kubectl get backup`) and ES snapshot (`GET _snapshot/roracle-s3/_all`); trigger `kubectl cnpg backup <fullname>-pg --method plugin --plugin-name barman-cloud.cloudnative-pg.io` before a minor/major upgrade.
3. Bump `/VERSION` (SemVer); `scripts/check-version.sh` (CI `helm` job) requires it to equal Chart `version` and `appVersion`. Tag `vX.Y.Z`; CI builds backend and web images.
4. `helm upgrade roracle deploy/helm/roracle -n roracle -f my-values.yaml --timeout 30m`. The `migrate` pre-upgrade hook runs first: `roracle migrate --wait SECONDS` retries the DB connection every 5 s and then exits non-zero with a password-free message (without `--wait` it fails at once). With `backoffLimit: 0` a failed migration blocks the rollout and leaves the old pods running; then api/worker/web roll.
5. Verify `/healthz` version, upload + search + chat smoke test.
Migration 0011 backfills `document_coauthors` from the drafts of already-published papers, repairs `document_versions.is_latest` (one per document: its highest searchable version) and queues one `acl_sync` job per backfilled document, so Elasticsearch gains `coauthor_users` once the worker drains the queue. No manual step; re-running is idempotent. Separately, the API rejects JSON bodies above `max(3 MiB, 2 x ROR_DRAFT_MAX_BYTES + 64 KiB)`.

Migration 0012 (sensitivity labels) runs as one transaction: it creates `labels`, seeds `General`, adds `label_id` to `documents` (backfilled and set NOT NULL, plus an index), `annotations` and `connectors`, and widens the proposals kind check. The `documents` changes hold an exclusive lock until the migration commits, so reads and writes on `documents` block for a time that grows with the row count. Run the upgrade in a maintenance window (or at least off-peak) and raise `migrate.activeDeadlineSeconds` (values.yaml, migrate-job.yaml) and `helm --timeout` accordingly. The backfill rewrites every `documents` row, so bloat and WAL scale with the row count (`annotations` and `connectors` ALTERs also lock, briefly). It also inserts one `acl_sync` job per non-deleted document in the same transaction. Until those jobs drain, Elasticsearch lacks `label_id` and the Postgres post-filter is authoritative (search may over-fetch briefly, never leaks). After the upgrade watch the drain: `SELECT status, count(*) FROM jobs WHERE kind='acl_sync' GROUP BY 1;` (or `roracle_jobs{kind="acl_sync"}` and `roracle_jobs_oldest_queued_seconds`) until `queued` is 0 and `failed` stays empty; scale workers if it is slow, and retry failures with `POST /admin/jobs/retry-failed {"kind":"acl_sync"}`. Downgrading drops all labels and label proposals.

Rollback: `helm rollback` restores manifests but does NOT revert database migrations. Migrations are additive within a minor line, so the previous release normally still runs;
otherwise restore Postgres with PITR (3.1) to just before the upgrade. Embedding-model changes need a re-embed (ADR-0006; the automated flow is `POST /admin/reindex`).
Also: after ECK CA rotation restart api/worker; update ClamAV signatures by refreshing the mirror (freshclam in the pod pulls from `parsers.clamav.mirrorUrl`; with no mirror configured signatures are only those baked into the image, so rebuild the image regularly).

## 3. Backup and restore

### RPO / RTO

| Store | Mechanism (chart defaults) | RPO | Restore |
|---|---|---|---|
| Postgres | CNPG WAL archiving to MinIO via barman-cloud plugin; `archive_timeout` forced to 5 min; immediate base backup at install + daily 02:00 (`postgres.backup.schedule`); retention 30d | <= 5 min (when WAL is being produced) | PITR in place via `postgres.recovery` (3.1) |
| Elasticsearch | SLM `roracle-daily` 01:30 to repo `roracle-s3`, expire 30d (min 7, max 60) | <= 24 h; index is rebuildable from Postgres + MinIO | snapshot restore (3.2) or rebuild |
| MinIO | versioning; backup buckets with GOVERNANCE retention; site replication to a DR site (manual setup) | near-zero with replication, else loss of the cluster = loss of originals | 3.3 |
| Keycloak | your Keycloak policy | | |

RTO target <= 4 h. Order: Postgres -> MinIO (if lost) -> Elastic -> scale api/worker up. Alert on WAL archive age (Grafana panel, 300 s line).
After the first install verify that a base backup exists (`kubectl get backup`; else trigger one with the command in section 2, Release upgrade): the immediate backup can run before the `minio-policy` hook has created the MinIO user. WAL archiving retries automatically.

A single-cluster MinIO also holds the backups: without replication to another site, a cluster loss loses data and backups together.

### 3.1 Postgres PITR (in place, Helm-managed name)

Under the default-deny NetworkPolicy only the Helm-managed cluster `<fullname>-pg` is reachable, so the restore reuses that name. The chart bootstraps it from the backups (`postgres.recovery`) instead of `initdb`. The new cluster must archive under a new `serverName`, otherwise it collides with the old WAL archive.

1. Confirm the backups exist and are recent: `kubectl get backup`, `kubectl cnpg status <fullname>-pg`. Deleting the Cluster deletes its PVCs.
2. Stop writers (disable the HPA first, or it scales back): `kubectl scale deploy/<fullname>-api deploy/<fullname>-worker --replicas=0`.
3. `kubectl delete cluster <fullname>-pg`.
4. `helm upgrade <release> deploy/helm/roracle -n roracle -f my-values.yaml --timeout 30m --set postgres.recovery.enabled=true --set-string postgres.recovery.targetTime="2026-11-15 09:30:00+00" --set postgres.backup.serverName=<fullname>-pg-r1` (omit `targetTime` for latest; `postgres.recovery.sourceServerName` defaults to `<fullname>-pg`). The Cluster is re-created by this upgrade, so with recovery enabled the `migrate` hook runs as `post-upgrade` (after the cluster is up).
5. Verify: `kubectl cnpg status <fullname>-pg`, row counts of `documents`, `jobs`; trigger a base backup `kubectl cnpg backup <fullname>-pg --method plugin --plugin-name barman-cloud.cloudnative-pg.io` and confirm it completes (`kubectl get backup`).
6. Keep `postgres.backup.serverName` (the new archive path) in your values permanently; set `postgres.recovery.enabled=false` again so a later cluster re-creation does not replay old backups. Scale api/worker back up.

Each further restore needs another new `serverName` (`-r2`, ...). Rows written after the target time are lost; blobs in MinIO newer than the target are orphans (the GC job removes objects without a `blobs` row). Migrations run on the upgrade when restoring across versions.

### 3.2 Elasticsearch restore

Use the `elastic` superuser (secret `roracle-es-elastic-user`), not `roracle_app`.
```bash
curl -s -u elastic:$PW "$ES/_snapshot/roracle-s3/_all?pretty" | grep snapshot\"   # list; repo is registered by the install hook
curl -s -u elastic:$PW -XDELETE "$ES/chunks-*"       # existing indices must be deleted/closed first
curl -s -u elastic:$PW -XPOST "$ES/_snapshot/roracle-s3/<snapshot>/_restore" \
  -H 'content-type: application/json' -d '{"indices":"chunks-*","include_global_state":false}'
curl -s -u elastic:$PW "$ES/_cluster/health?wait_for_status=yellow&timeout=30m"
```
On a rebuilt cluster re-run the hook (`helm upgrade`) first so repository `roracle-s3` exists. Documents ingested after the snapshot are missing:
re-queue them with the stage-reset statement of "Re-embed after embedding prompt/model change" (stops workers), or run `POST /admin/reindex` with the current model to rebuild into a fresh generation. After restoring a pre-cutover snapshot, verify that `chunks` points at the index PG records as `embed_index` (`GET /admin/reindex`); if not, run a reindex.
Elastic holds no unique data, so when no usable snapshot exists use that same procedure to rebuild everything from Postgres + MinIO (search is down until versions are searchable again).

### 3.3 MinIO

- Lost objects/versions: restore the previous version (`mc undo` / `mc cp --version-id`) from the versioned bucket.
- Cluster loss: point `config.ROR_S3_ENDPOINT` (and `postgres.backup.endpointURL`, `elasticsearch.snapshot.endpoint`) at the DR site, which holds the replicated `roracle` bucket via site replication. Credentials/policies are not replicated unless you replicate IAM: create the `minio.users.*` there.
- Backup buckets (`roracle-pg-backup`, `roracle-es-snapshots`) are replicated the same way; GOVERNANCE retention blocks deletion by normal users for `minio.backupRetentionDays`.

### 3.4 Quarterly drill checklist

- [ ] Restore Postgres PITR (3.1) to a time 1 h ago in a scratch namespace/release; compare row counts of `documents`, `jobs`.
- [ ] Restore the latest ES snapshot into a scratch ES; run a known query, compare hit counts.
- [ ] Verify MinIO replication status (`mc admin replicate status`) and open a replicated object.
- [ ] Point a scratch api/worker at the restored stores; smoke test upload, search, chat, citation.
- [ ] Record elapsed time against RTO 4 h and WAL archive age against RPO 5 min; file gaps.

## 4. Incident playbooks

**Uploads stuck in "Scanned/Parsed"** — check sidecar health (`kubectl get pods -l app=docling`),
then `last_error` on failed jobs. OCR timeouts on huge scans: raise the Docling timeout or split
the file; retry the job.

**Search slow** — Postgres first: `SHOW jit;` must be `off` and `shared_buffers` ~ 25% of memory (JIT made search ~4x slower under load); then Elastic: hot
threads, heap pressure, page-cache misses (quantized vectors must fit in RAM); API CPU per pod (about 10 searches/s each: add replicas); reduce rerank depth
temporarily (admin → Retrieval) or clear `ROR_RERANK_URL` (fallback keeps the fused order).

**Chat errors** — inference endpoint status; model removed from allowlist; check
`roracle_llm_errors_total{model=…}`.

**Suspected data exposure** — revoke PATs of the user (admin), set affected documents `private`,
export audit log for the window (`audit_log` by `target_id` and time), escalate to security.

**Malware detected** — job fails at *Scanned*, file quarantined (blob not linked), uploader
notified; security gets an audit event. Nothing to clean unless detection happened after
ingestion (signature update): admin → Documents → *Quarantine* removes it from search.

## 5. Admin API

All `/admin/*` routes need an admin JWT (PATs get 403) and every call is written to `audit_log`.
Lists page with `?before_id=&limit=` (default 50, max 200) and return `{items, next_before_id}`.

| Endpoint | Operator use |
|---|---|
| `GET /admin/health` | Dependency probe (db, elastic, storage), queue depth/age, connector scheduler tick. `degraded` = at least one check failed. |
| `GET /admin/jobs?status=failed&kind=` | Inspect failed jobs and their `last_error`. |
| `GET /admin/jobs/summary` | Jobs per kind/status and versions per pipeline stage. |
| `POST /admin/jobs/{id}/retry` | Retry one failed job (409 otherwise); ingest jobs get a fresh attempt budget. |
| `POST /admin/jobs/retry-failed {kind?}` | Requeue all failed jobs (optionally one kind) after fixing the cause, e.g. Ollama or ES outage. |
| `GET /admin/review?source=all\|upload\|connector&page=` | Versions parked for near-duplicate review (offset paged); decide via the near-dup endpoint. |
| `POST /admin/maintenance/{purge\|gc\|reconcile}` | Queue a one-off retention purge, blob GC or ES tag reconcile (`202 {job_id}`); see Maintenance in section 2. |
| `GET /admin/stats` | Document/blob counts, storage bytes, top uploaders. |
| `GET /admin/users`, `/admin/groups` | Users mirror (as of last login), per-user usage, group sizes. |
| `GET /admin/tags`, `POST /admin/tags/merge {sources, target}` | Consolidate duplicate tags; ES tags follow via `acl_sync`. |
| `GET /admin/audit?action=<prefix>&user_id=` | Audit trail search. |
| `GET/POST/PUT/DELETE /admin/connectors*` | Manage connectors, trigger runs, inspect `runs`; retrying a failed `connector_run` job returns 409 (skipped in bulk) while that connector has an active run. |
| `GET/PUT /admin/settings` | Runtime setting overrides. |
| `POST/GET /admin/reindex`, `DELETE /admin/reindex/old` | Embedding-model switch (background re-embed, alias cutover); see "Change the embedding model". |
| `GET /admin/needs-title` | Documents awaiting a title. |
| `GET /admin/ratings` | Rating overview. |
| `GET /me/notifications`, `POST /me/notifications/read` | Caller's inbox (any user); admins also receive `ops.job_failed`; kinds also include `proposal.new`, `note.reply`, `note.on_document` (public notes only). |

Paging: `/admin/users` and `/admin/review` use offset `page=`; the other lists use keyset `before_id=`.
