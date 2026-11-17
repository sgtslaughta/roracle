# Runbook: incidents and job triage

Audience: on-call operators. Part of the [operations runbook](operations-runbook.md). Commands assume namespace `roracle`, Helm release `roracle`. Signals and alerts: [health runbook](runbook-health-alerts.md). Restores: [backup and DR](backup-dr.md). Job kinds, retries and idempotency: [jobs reference](../reference/jobs.md); mechanics: [jobs and queue](../architecture/jobs-queue.md).

## 1. Playbooks

**Uploads stuck before "Searchable".** Read the stage per version (`GET /api/admin/jobs/summary`, `versions` by stage) and the `last_error` of failed ingest jobs (`GET /api/admin/jobs?status=failed&kind=ingest`). Stages run `uploaded`, `scanned`, `parsed`, `summarized`, `deduped`, `chunked`, `embedded`, `searchable`; `review`, `needs_title`, `rejected` and `cancelled` are parked or final. A stuck `uploaded` or `scanned` stage points at ClamAV or Docling: check the sidecars (`kubectl get pods -l app.kubernetes.io/component=docling`, same for `clamav`, `gotenberg`, `grobid`). OCR timeouts on huge scans: raise `ROR_DOCLING_TIMEOUT_S` or split the file, then retry the job. A version whose ingest crashed five times fails with "ingest crashed repeatedly": retry through the API so it gets a fresh budget ([routine runbook](runbook-routine.md#retry-failed-jobs)).

**Search slow.** Postgres first: `SHOW jit;` must be `off` and `shared_buffers` about 25% of memory (JIT made search about 4x slower under load, [perf report](../perf/report-2026-11-17.md) F1). Then Elastic: hot threads, heap pressure, page-cache misses (the quantized vectors must fit in RAM, [capacity planning](capacity-planning.md)). Then API CPU per pod (about 10 searches/s each: add replicas). Reduce the rerank depth temporarily (the `rerank_depth` runtime setting, [runtime settings](../admin/runtime-settings.md)) or clear `ROR_RERANK_URL` (a failing or absent reranker keeps the fused order).

**Chat errors.** Check the inference endpoint (`ROR_LLM_BASE_URL`), that the model is still in `ROR_CHAT_MODELS`, and the api logs. There is no per-model error metric: look for 5xx on `/chats/{chat_id}/messages` in `roracle_http_requests_total`.

**Reranker degraded.** `roracle_rerank_fallback_total{reason}` rising (`timeout`, `http`, `malformed`) means searches keep the fused order, so quality drops but availability does not. Check the reranker host (`deploy/game-01`), `ROR_RERANK_TIMEOUT_S` (default 3 s) and its batch limit.

**Suspected data exposure.** Revoke the user's personal access tokens (admin), set the affected documents `private`, export the audit log for the window (`audit_log` by `target` and `at`; routes `GET /api/admin/audit?user_id=...`), and escalate to security ([security architecture](security.md)). Elasticsearch ACLs converge through `acl_sync` jobs; the Postgres post-filter is authoritative meanwhile.

**Malware detected.** The scan stage marks the version `rejected` (hidden from reads) and fails the ingest job without retry with `malware detected: <signature>`; the uploader is notified. A file that exceeds the scanner limits fails the same way with a size message: raise `parsers.clamav.limits`. Evidence uploads get `scan_status = infected` and their download is blocked. Nothing rescans stored files after a signature update: if a newer signature flags a document that is already searchable, delete it (`DELETE /api/documents/{doc_id}`, owner or admin) and let the retention purge finish the job ([routine runbook](runbook-routine.md#maintenance-retention-purge-blob-gc-tag-reconcile-maintenance-job)).

**Queue not draining.** `roracle_jobs_oldest_queued_seconds` high: check worker pods and `GET :8001/healthz`, then scale (`kubectl -n roracle scale deploy/roracle-worker --replicas=8`; claims use `SKIP LOCKED`, so extra replicas are safe), then look at `last_error` of failed jobs. Bulk kinds (`rebuild_units`, `reconcile_tags`, `reparse_refs`) are claimed only when no user job is due, so a bulk backlog never explains a stuck upload.

**Elasticsearch lost or corrupt.** Search is down, uploads still queue. Restore a snapshot or rebuild: [backup and DR](backup-dr.md), sections 2 and 6.

**Postgres lost.** PITR: [backup and DR](backup-dr.md), section 5.

**Object store lost.** [backup and DR](backup-dr.md), section 7. Blob reads and ingest fail until it is back; `GET /readyz` reports `s3: fail`.

## 2. Job leases, drain and worker shutdown

Jobs run under a **lease** (`ROR_JOB_LEASE_S`, default 90 s, renewed every lease/3 by the worker). A crashed or stuck worker's job is requeued after the lease (plus up to 30 s for the sweep), spending an attempt, and a worker that lost its lease discards its result. On SIGTERM a running job gets `ROR_JOB_DRAIN_S` (code default 0 = wait, chart 120 s), then is **released** back to the queue without spending an attempt (counted in `released`; after 200 releases the job fails with a clear error instead of looping). A job that defers itself more than 200 times (for example a busy per-owner lock) fails the same way.

- Ingest stages, rebuild pages and the re-embed copy and convergence resume at their last checkpoint (the pre-swap convergence is sliced to about 60 s with a doc-id cursor).
- `eval_run` (a judged run) and a single long LLM batch are **not** resumable: they restart from the beginning. Do not scale workers down or roll them while one runs. The worker HPA's CPU signal scales down exactly the pods waiting on ES or the LLM; the chart default is a 1 h scale-down stabilization (one pod per 10 min). Prefer queue-depth scaling with KEDA and disable the CPU HPA.
- Keep `worker.terminationGracePeriodSeconds` at or above the drain plus 30 (chart 150).
- Stuck job triage: `SELECT id, kind, status, attempts, released, lease_until, last_error FROM jobs WHERE status='running';`. An expired `lease_until` is swept at the next idle worker loop.
- Rolling upgrade across migrations 0014 to 0016: run `roracle migrate` (0014 builds queue indexes with `CREATE INDEX CONCURRENTLY`, which waits for open transactions: drain workers or let long jobs finish first), then roll workers with the drain. Do not run old and new workers side by side longer than the drain (an old worker's completion is not fenced by the new claim token). See [upgrades](upgrades.md).
- Postgres settings these numbers assume: `jit = off`, `shared_buffers` about 25% of memory ([capacity planning](capacity-planning.md)); the connection budget formula is in [the connection budget](../perf/connection-budget.md).

## 3. Router, global answers and agentic retrieval (when enabled)

Off by default (`ROR_ROUTER`, `ROR_AGENTIC_RETRIEVAL`, admin runtime overrides). When on, busy admission falls back to the plain path (the SSE `meta` event says `global: {skipped: busy}` or `agentic: false`), so a burst degrades answer quality, not availability. An agentic loop holds up to `ROR_AGENTIC_MAX_PARALLEL` extra pooled database connections per API process; watch pool waits (`ROR_DB_POOL_TIMEOUT_S`) before raising it. The planner needs a small fast model (`ROR_AGENTIC_PLANNER_MODEL`); with a slow one calls hit `ROR_AGENTIC_CALL_TIMEOUT_S` (1.5 s) and the loop silently stops.

## 4. After an incident

- Retry what failed once the cause is fixed: `POST /api/admin/jobs/retry-failed` ([health runbook, admin API](runbook-health-alerts.md#4-admin-api)).
- After any Elasticsearch restore or suspected ACL drift, run the tag reconcile (`POST /api/admin/maintenance/reconcile`).
- Record what was lost against the RPO and RTO in [backup and DR](backup-dr.md#4-rpo-and-rto).
