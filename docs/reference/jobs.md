# Jobs reference

Every background job kind in rOracle: who enqueues it, its payload, how it is retried and whether re-running it is safe. Source of truth: `backend/src/roracle/worker.py` (the handler table), `queue.py` (claiming, leases, retries) and the handler modules. Back to the [reference index](README.md). Mechanics and diagrams: [jobs and queue](../architecture/jobs-queue.md). Operating them: [routine runbook](../enterprise/runbook-routine.md), [incident runbook](../enterprise/runbook-incidents.md). Series about jobs: [metrics reference](metrics.md).

## The `jobs` table

Postgres is the queue (ADR-0003). One row per job in `jobs`:

| Column | Meaning |
|---|---|
| `kind` | one of the kinds below |
| `payload` | JSON object, shape per kind |
| `status` | `queued`, `running`, `done` or `failed` |
| `attempts` | number of claims; never decremented |
| `released` | claims given back for free (shutdown release, deferral); `attempts - released` is what counts against the limit |
| `run_after` | the job is claimable when this is in the past; retries and delays move it forward |
| `lease_until`, `claim_token` | the running worker's lease and the fencing token of that claim |
| `last_error` | the last failure message, cut to 2000 characters |

`GET /api/admin/jobs` shows `id`, `kind`, `status`, `attempts`, `last_error`, `payload`, `created_at` and `updated_at`.

## Claiming and priority

- A worker claims one due job at a time with `FOR UPDATE SKIP LOCKED`, ordered by `run_after`, then `id`. More workers never conflict.
- **User kinds** (everything except the three below) are claimed first.
- **Bulk kinds** `reconcile_tags`, `rebuild_units` and `reparse_refs` are claimed only when no user job is due, `reconcile_tags` before `rebuild_units` and `reparse_refs`. A backfill therefore never delays an upload.
- A worker only claims kinds it has a handler for; the worker registers all 15 kinds below.

## Leases and retries (all kinds unless noted)

| Rule | Value |
|---|---|
| Lease | `ROR_JOB_LEASE_S` (default 90 s), renewed every lease/3; a lost lease makes the handler abort at its next checkpoint and its result is discarded |
| Expired lease | requeued by any worker's sweep (idle loop every 30 s, or on any busy worker's heartbeat); it counts as a failed attempt and as `retry` or `failed` in `roracle_job_runs_total` |
| Failure | the handler's transaction rolls back, then the job is requeued with a delay of `10 x 2^(attempts - released)` seconds (20, 40, 80, 160 s) |
| Attempt limit | 5: the job is `failed` when `attempts - released` reaches 5 |
| `NonRetryable` | fails at once; the message is stored as `last_error` with a `user:` prefix, which marks it as safe to show the uploader |
| Shutdown | SIGTERM stops claiming; a running job gets `ROR_JOB_DRAIN_S` (chart 120 s), then is released without spending an attempt |
| Free refunds | a job released or deferred (`RequeueLater`) 200 times fails instead of looping |
| Permanent failure | an `ingest` job notifies the uploader; any other kind notifies every admin (`ops.job_failed`); an ingest for a connector document also notifies the admins |
| Retention | the periodic `maintenance` job deletes `done` rows after 7 days and `failed` rows after 30 days |
| Manual retry | `POST /api/admin/jobs/{job_id}/retry` or `POST /api/admin/jobs/retry-failed`: status back to `queued`, `attempts` 0, `run_after` now, `last_error` cleared; an ingest job also gets a fresh per-version crash budget |

Delivery is at least once: a handler may run again after a crash, so every handler below is written to be repeated safely.

## Kinds at a glance

| Kind | Priority | Payload | Enqueued by |
|---|---|---|---|
| `ingest` | user | `{version_id}` | upload, connector run, near-duplicate decision, manual retry |
| `citations` | user | `{version_id}` | `ingest`, in the transaction that makes the version searchable |
| `acl_sync` | user | `{doc_id}` | any ACL, title, tag, label, owner or visibility change; reconcile repairs; migrations |
| `evidence_scan` | user | `{evidence_id}` | evidence file upload |
| `connector_tick` | user | `{}` | worker start, then itself every `ROR_CONNECTOR_TICK_S` |
| `connector_run` | user | `{connector_id}` | the tick, `POST /api/admin/connectors/{cid}/run` |
| `maintenance` | user | `{op}` | worker start, itself, `POST /api/admin/maintenance/{op}` |
| `lsh_backfill` | user | `{}` or `{after}` | migration 0014e (once) |
| `reconcile_tags` | bulk | `{}` or `{after}` | `maintenance`, re-embed |
| `reparse_refs` | bulk | `{version_id}` | `roracle reparse-refs` |
| `rebuild_units` | bulk | `{version_ids, what, into, batch}` | `roracle rebuild`, `POST /api/admin/retrieval/rebuild` |
| `rebuild_watch` | user | `{what, into, batch}` | the rebuild enqueue, itself |
| `reembed` | user | `{phase, source, target, cursor, ...}` | `POST /api/admin/reindex`, itself |
| `eval_run` | user | `{set_id, trigger, path, judge}` | `POST /api/admin/eval/runs`, re-embed, rebuild watch |
| `eval_generate` | user | `{set_id, principal, n}` | `POST /api/admin/eval/sets/{set_id}/generate` |

## Per kind

### `ingest`

- **Payload:** `version_id` (UUID of a `document_versions` row). A malformed id is `NonRetryable`.
- **Does:** advances the version through `uploaded`, `scanned`, `parsed`, `summarized`, `deduped`, `chunked`, `embedded`, `searchable`, writing each stage as it goes. Versions already in a terminal stage (`searchable`, `review`, `needs_title`, `rejected`, `cancelled`) or whose document is deleted are skipped. Detail: [ingest data flow](../architecture/ingest.md).
- **Retries:** the generic rules. Malware, an unconvertible file, no extracted text, an embedding dimension mismatch and a missing version are `NonRetryable`. On top of the queue limit each version has a crash budget: the start counter `ingest_attempts` is bumped at every run and the sixth start fails with "ingest crashed repeatedly". A busy per-owner dedupe lock defers the job without spending an attempt.
- **Idempotent:** yes. A re-run resumes at the recorded stage; making a version searchable and enqueuing its `citations` job is one transaction, so there is never a searchable version without it.

### `citations`

- **Payload:** `version_id`.
- **Does:** resolves the version's references to documents (DOI, arXiv, PMID, PMCID, ACL Anthology, OpenReview ids, then title and year) and relinks edges into the document from other papers.
- **Retries:** generic. Does nothing when the version is not searchable or the document is deleted.
- **Idempotent:** yes (recomputes the links).

### `acl_sync`

- **Payload:** `doc_id`.
- **Does:** reads the document's ACL fields, label, title and tags from Postgres and pushes them to every chunk in Elasticsearch through an `update_by_query` on the mutation alias `chunks-all`. Postgres is re-read on every try, so a concurrent revocation is never overwritten with older data. A missing or deleted document is a no-op.
- **Retries:** generic (an Elasticsearch outage retries with backoff). Until it converges the Postgres post-filter enforces the current visibility.
- **Idempotent:** yes; unchanged documents update nothing. Some producers delay it by 30 s (a durable second try after the request's own best-effort sync).

### `evidence_scan`

- **Payload:** `evidence_id`.
- **Does:** streams the evidence blob to ClamAV and sets `evidence.scan_status`: `clean`, `infected`, `failed` (blob missing or over the scanner limit) or `unscanned` (no `ROR_CLAMAV_HOST`).
- **Retries:** scanner connection errors retry with the generic backoff; size and missing-blob outcomes are recorded as `failed` without failing the job.
- **Idempotent:** yes.

### `connector_tick` and `connector_run`

- `connector_tick` has an empty payload. It enqueues one `connector_run` for each enabled connector that is due and has no active run, then schedules the next tick `ROR_CONNECTOR_TICK_S` (default 60 s) later if none is queued. Worker start makes sure one exists.
- `connector_run` has the payload `connector_id`. It lists the source and imports items, capped by `ROR_CONNECTOR_MAX_ITEMS` (1000 per run) and `ROR_CONNECTOR_MAX_RUN_S` (3600 s); the rest waits for the next run.
- **Retries:** the handler never raises, so the job always ends `done` and the result is in the connector's run record (`ok`, `partial` or `failed`, with a log). The queue only retries it after a lease expiry (a crashed worker); the next run marks the stale run `failed` ("worker interrupted"). A manual retry or run answers 409 while the connector has a queued or running run.
- **Idempotent:** yes; unchanged items are skipped.

### `maintenance`

- **Payload:** `op` is `all` (periodic), `purge`, `prune_jobs`, `gc` or `reconcile`. The manual endpoint accepts `purge`, `gc` and `reconcile`.
- **Does:** `all` runs purge, job pruning, blob GC and reconcile, then reschedules itself `ROR_MAINTENANCE_INTERVAL_S` (default 86400 s) later. A step that processes a full batch (100 purged documents, 500 blobs, 10,000 jobs) queues a follow-up job for that step; reconcile queues a `reconcile_tags` chain. Worker start creates the periodic job if none is queued or running.
- **Retries:** in `all` a failing step is logged and the others still run; a manual op raises and retries generically.
- **Idempotent:** yes. Purge is off unless `ROR_RETENTION_DELETED_DAYS` is above 0. Details: [routine runbook](../enterprise/runbook-routine.md#maintenance-retention-purge-blob-gc-tag-reconcile-maintenance-job).

### `lsh_backfill`

- **Payload:** `{}` at first, then `after` (the last version id done).
- **Does:** writes the near-duplicate LSH bands of every version that has a MinHash, 500 per job, chaining until done, then marks the index ready. Until then ingest uses the exhaustive near-duplicate scan.
- **Idempotent:** yes. It is enqueued once, by migration 0014e.

### `reconcile_tags` (bulk)

- **Payload:** `{}` or `{after}` (document id cursor).
- **Does:** checks the next 500 live documents against Elasticsearch by their ACL hash and queues an `acl_sync` for each one that differs, skipping documents that already have one queued. A full batch queues the next link of the chain. An in-sync corpus costs no Elasticsearch writes.
- **Retries:** generic. **Idempotent:** yes; it only detects drift.

### `reparse_refs` (bulk)

- **Payload:** `version_id`.
- **Does:** re-runs GROBID on the stored PDF, updates only `ids`, `doi` and `references`, and relinks citations in both directions. Added by `roracle reparse-refs`, which exits with status 2 when `ROR_GROBID_URL` is unset. Does nothing for a deleted, non-searchable or non-PDF version, or when the worker has no GROBID URL.
- **Retries:** generic. **Idempotent:** yes; an answer without parsed ids changes nothing and an empty reference list never wipes existing references.

### `rebuild_units` (bulk) and `rebuild_watch`

- `rebuild_units` payload: `version_ids` (25 latest searchable, non-deleted versions), `what` (`context` and/or `raptor`), `into` (a shadow index name or null for the active index) and `batch`. It rewrites the derived chunks or summary units of each version into the target index.
- **Retries:** a version that cannot be rebuilt (parsed document missing) is skipped, and after the others finish the job fails without retry naming the count. A missing `ROR_CONTEXT_MODEL` for `context` fails the job without retry. Each version is idempotent.
- `rebuild_watch` payload: `what`, `into`, `batch`. It requeues itself every 60 s while `rebuild_units` jobs of that batch are queued or running, then queues an `eval_run` on the reference set when the rebuild targeted the active index and `ROR_EVAL_ON_REINDEX` is on.
- Rebuilds are refused while a re-embed runs, and a re-embed while rebuild jobs are pending.

### `reembed`

- **Payload:** `phase` (`copy`, `converge`, `cutover`, `catchup`), `source` (index), `target` (`model`, `dims`, `index`), `cursor`, `copied`, `total`, `round` and `slice` (`i`, `n`); converge jobs also carry `conv` (the checkpoint).
- **Does:** `POST /api/admin/reindex` enqueues one `copy` chain per slice (`ROR_REEMBED_SLICES`, default 4, at most 16). Each job copies a page of 1000 chunks, re-embedding them, and enqueues the next with the new cursor. When the last slice finishes, `converge` jobs (about 60 s each) reconcile against Postgres, then `cutover` persists the active model and swaps the read alias, then two `catchup` rounds per slice run 300 s apart. A failed copy slice refuses the cutover.
- **Retries:** generic for transient errors; permanent errors (an incomplete pass, an alias that drifted) are `NonRetryable`. **Idempotent:** yes, the copy skips chunks already present and takes ACL, tags and title from Postgres. Procedure: [routine runbook](../enterprise/runbook-routine.md#change-the-embedding-model-zero-downtime).

### `eval_run`

- **Payload:** `set_id`, `trigger` (`admin`, `model_change`, `reindex`), `path` (`chat` default or `search`) and `judge` (a judged run needs `chat`).
- **Retries:** **never retried.** Any failure, including a bad payload, a deleted set or an unresolvable principal, is `NonRetryable`; the failed run row is kept in the eval history. A run with an ACL violation is a `failed` run. Not resumable: a lease loss restarts it from the start.
- **Idempotent:** each run writes its own run row.

### `eval_generate`

- **Payload:** `set_id`, `principal` (a non-admin user id) and `n` (1 to 200).
- **Does:** samples visible documents the principal may send to the model, asks the LLM for draft questions and stores them as drafts for an admin to review. Documents with the `exclude_llm` label gate never reach the model; the gate is re-read from Postgres just before each passage is sent.
- **Retries:** invalid input is `NonRetryable`; other errors retry generically. **Idempotent:** it only adds drafts, so a re-run adds more.
