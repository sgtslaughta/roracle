# Runbook: health signals, alerts and the admin API

Audience: on-call operators. Part of the [operations runbook](operations-runbook.md). Commands assume namespace `roracle`, Helm release `roracle`, CNPG cluster `roracle-pg`, ECK cluster `roracle` (service `roracle-es-http`). Adjust to your names. Every metric is defined in the [metrics reference](../reference/metrics.md); dashboards and scrape wiring are in [observability](observability.md).

## 1. Health at a glance

The "healthy" column is a suggested operating range, not something the code enforces.

| Signal | Where | Healthy |
|---|---|---|
| API liveness | `GET /healthz` | `{"status":"ok","version":"x.y.z"}`; no I/O |
| API readiness | `GET /readyz` | 200; on 503 the body is `{"checks": {...}}` and the failing check among `db`, `es`, `s3` says `fail` (2 s timeout each, no error text) |
| Worker | `GET :8001/healthz` (`ROR_WORKER_HEALTH_PORT`) | 200; 503 only when the loop heartbeat is older than 300 s and no job is running |
| Dependency probe (admin) | `GET /api/admin/health` | `status: ok`; `degraded` when a check fails or the database is down |
| Queue depth | `roracle_jobs{kind,status}` (or `SELECT kind, status, count(*) FROM jobs GROUP BY 1,2;`) | `queued` drains; few `failed` |
| Oldest queued job | `roracle_jobs_oldest_queued_seconds{kind}` | below 5 min |
| Job outcomes | `rate(roracle_job_runs_total{outcome=~"retry|failed"}[5m])` (worker) | near 0 |
| Ingest failures | `roracle_ingest_stage_failures_total{stage}` (worker) | near 0 |
| API traffic and errors | `roracle_http_requests_total{method,route,status}` | 5xx below 1% |
| API latency | `roracle_http_request_duration_seconds{method,route}`; `route` is the matched template without the `/api` prefix, for example `/search` | search p95 below 500 ms |
| Reranker (when enabled) | `rate(roracle_rerank_fallback_total{reason}[5m])`, `roracle_rerank_seconds` | fallbacks near 0; p95 below 800 ms for 200 passages |
| Near-duplicate lookups | `roracle_neardup_exhaustive_total{reason}` | near 0 (a rise means the LSH backfill is missing or failed, so the slow exhaustive scan runs) |
| Elastic | `GET _cluster/health` | green |
| Postgres | `kubectl cnpg status roracle-pg` | primary plus replicas streaming |

Not instrumented, so do not alert on a metric by these names: search-specific latency, chat time-to-first-token, ingest end-to-end time and LLM errors have no dedicated series. Use the `route` label of the HTTP histogram (for example `/search`). For streaming responses (SSE chat) the middleware returns as soon as the response headers are ready, so the duration is time-to-headers, not stream length. Ingest age is visible as the age of `roracle_jobs_oldest_queued_seconds{kind="ingest"}` and as versions per stage in `GET /api/admin/jobs/summary`. LLM failures show in the api and worker logs and as failed jobs.

Example queries:

```promql
histogram_quantile(0.95, sum by (le) (rate(roracle_http_request_duration_seconds_bucket{route="/search"}[5m])))
sum(rate(roracle_http_requests_total{status=~"5.."}[5m])) / sum(rate(roracle_http_requests_total[5m]))
max by (kind) (roracle_jobs_oldest_queued_seconds)
```

Metrics come from `/metrics` on the api (also the database-backed `roracle_jobs*` gauges, collected at scrape time with a 2 s statement timeout; with several api replicas aggregate with `max by`) and on the worker (`roracle_job_runs_total`, `roracle_ingest_stage_failures_total`). Both endpoints are cluster-internal only. The `/metrics` route itself is not counted.

## 2. Suggested alerts

The chart ships a Grafana dashboard but no Prometheus alert rules: there is no `PrometheusRule` in `deploy/`. Define these in your Alertmanager setup ([observability](observability.md), section 4):

| Condition | Meaning |
|---|---|
| `roracle_jobs_oldest_queued_seconds` above 900 s | queue stuck or workers down |
| failed jobs above 50 per hour (`increase(roracle_job_runs_total{outcome="failed"}[1h])`) | a handler fails persistently |
| search p95 above 1 s for 10 min | see "Search slow" in [incidents](runbook-incidents.md) |
| 5xx rate above 1% | API or dependency fault |
| Elastic yellow for 15 min, red at once | shard allocation or node loss |
| Postgres replication lag above 60 s; WAL archive age above 300 s | RPO at risk ([backup and DR](backup-dr.md)) |
| Ceph `HEALTH_WARN` or `HEALTH_ERR`, an OSD down (Rook-Ceph) or your S3 provider's health | object store |
| ClamAV signatures older than 3 days | scanner stale (see section 4 of [incidents](runbook-incidents.md)) |
| `roracle_rerank_fallback_total` above 5% of searches for 10 min | reranker down or slow |

## 3. Performance gate and budgets

Budgets are in `docs/perf/budgets.yaml` and the measured state in [the perf report](../perf/report-2026-11-17.md) ([budget summary](../perf/README.md#budgets-at-a-glance)). The nightly GitLab job `perf` runs `roracle perf suite --profile ci` against a small seeded scratch database and a scratch `perf-*` index with fake models and budgets multiplied by 1.5. Run the full profile on a staging copy only: the CLI refuses databases not matching `roracle_perf[a-z0-9_]*`, but `--es-url` is not guarded, so never point it at production. A failing row names its budget. Check first `jit` (must be `off`), Postgres `shared_buffers` and the API replica count (one API process sustains about 10 searches/s, 5 filtered). Sizing: [capacity planning](capacity-planning.md).

## 4. Admin API

All `/api/admin/*` routes need an admin JWT (PATs get 403) and every call is written to `audit_log`. Lists page with `?before_id=&limit=` (default 50, max 200) and return `{items, next_before_id}`; `/api/admin/users` and `/api/admin/review` use offset `page=`.

| Endpoint | Operator use |
|---|---|
| `GET /api/admin/health` | Dependency probe (db, elastic, storage), queue depth and age, connector scheduler tick. `degraded` = at least one check failed. |
| `GET /api/admin/jobs?status=failed&kind=` | Inspect failed jobs and their `last_error`. |
| `GET /api/admin/jobs/summary` | Jobs per kind and status, and versions per pipeline stage. |
| `POST /api/admin/jobs/{job_id}/retry` | Retry one failed job (409 otherwise); ingest jobs get a fresh attempt budget. |
| `POST /api/admin/jobs/retry-failed` with `{kind?}` | Requeue up to 1000 failed jobs (optionally one kind) after fixing the cause; returns `{retried}`, call again for the rest. |
| `GET /api/admin/review` | Versions parked for near-duplicate review. |
| `POST /api/admin/maintenance/{op}` | Queue a one-off retention purge, blob GC or ES tag reconcile (`purge`, `gc`, `reconcile`; `202 {job_id}`). See [routine procedures](runbook-routine.md#maintenance-retention-purge-blob-gc-tag-reconcile-maintenance-job). |
| `GET /api/admin/stats` | Document and blob counts, storage bytes, top uploaders. |
| `GET /api/admin/users`, `GET /api/admin/groups` | Users mirror (as of last login), per-user usage, group sizes. |
| `GET /api/admin/audit?action=<prefix>&user_id=` | Audit trail search. |
| `GET/PUT /api/admin/settings` | Runtime setting overrides. |
| `POST/GET /api/admin/reindex`, `DELETE /api/admin/reindex/old` | Embedding-model switch (background re-embed, alias cutover). See [routine procedures](runbook-routine.md#change-the-embedding-model-zero-downtime). |
| `POST /api/admin/retrieval/rebuild` | Rebuild derived index fields (context notes, RAPTOR) into the active index or a shadow. |

A retried `connector_run` job returns 409 (skipped in bulk) while its connector has an active run. A permanent failure of any job kind except `ingest` (and of an ingest for a connector document) sends every admin an `ops.job_failed` notification; a failed ingest of an upload notifies the uploader. The full list of routes is in the [API reference](../reference/api.md).
