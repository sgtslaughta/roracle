# Metrics reference

Prometheus metrics exposed by rOracle. Source of truth: `backend/src/roracle/metrics.py`; every series below is defined there. Back to the [reference index](README.md). Operating guidance: [health runbook](../enterprise/runbook-health-alerts.md); dashboards and scrape wiring: [observability](../enterprise/observability.md); job kinds behind the `kind` label: [jobs reference](jobs.md).

## Endpoints

| Process | Endpoint | Serves |
|---|---|---|
| api | `GET /metrics` on port 8000 | the HTTP, job, ingest, rerank and near-duplicate counters of the api process, plus the database-backed `roracle_jobs*` gauges |
| worker | `GET /metrics` on `ROR_WORKER_HEALTH_PORT` (default 8001, `0` turns the listener off) | the counters of the worker process; no `roracle_jobs*` gauges |

Both endpoints are cluster-internal: the chart never routes them through the ingress, and its NetworkPolicy lets only the `networkPolicy.monitoringNamespace` namespace scrape them. With `metrics.enabled` the pods carry `prometheus.io/*` annotations. Besides the series below, both endpoints include the standard `python_*` and `process_*` series of `prometheus_client`. Counters also export a `_created` series. The `/metrics` request itself is not counted in the HTTP series.

## Series

| Metric | Type | Labels | Process | Meaning |
|---|---|---|---|---|
| `roracle_http_requests_total` | counter | `method`, `route`, `status` | api | Requests by HTTP method, matched route template and response status code. |
| `roracle_http_request_duration_seconds` | histogram (default buckets, 5 ms to 10 s) | `method`, `route` | api | Handler latency per route. For streaming (SSE) responses it is the time until the response headers are ready, not the stream length. |
| `roracle_jobs` | gauge | `kind`, `status` | api (scrape time) | Rows in `jobs` with status `queued`, `running` or `failed`, per kind. `done` rows are not exported. |
| `roracle_jobs_oldest_queued_seconds` | gauge | `kind` | api (scrape time) | Age of the oldest queued job of the kind, measured from its `run_after`, never negative. |
| `roracle_job_runs_total` | counter | `kind`, `outcome` | worker | Job runs by outcome: `done`, `retry` (requeued with backoff) or `failed` (permanent). |
| `roracle_ingest_stage_failures_total` | counter | `stage` | worker | Exceptions raised while advancing an ingest version, by the stage the version was in. |
| `roracle_rerank_seconds` | histogram (buckets 0.05, 0.1, 0.2, 0.4, 0.8, 1.5, 3, 6 s) | none | api, worker | Latency of one reranker call, observed for successful and failed calls. |
| `roracle_rerank_fallback_total` | counter | `reason` | api, worker | Reranker calls that failed and kept the fused order. `reason` is `timeout`, `http` or `malformed`. |
| `roracle_neardup_exhaustive_total` | counter | `reason` | worker | Ingest near-duplicate checks that ran the slow exhaustive scan because the LSH marker is missing and no `lsh_backfill` job is queued or running (a backfill still in progress does not count). The only reason is `backfill_missing_or_failed`. |

Derived names you may see in queries: `roracle_http_request_duration_seconds_bucket`, `_sum` and `_count`, and the same suffixes for `roracle_rerank_seconds`.

## Behaviour that matters when you alert

- **Label values.** `route` is the FastAPI route template as the API sees it, so without the `/api` prefix: `/search`, `/chats/{chat_id}/messages`. Requests that match no route are counted as `unmatched`. `status` is the numeric code as a string.
- **Series appear on first use.** A counter or label combination has no series until its first event, and `roracle_jobs{kind,status}` has no series for a combination with zero rows (not a 0 sample). Alert with `absent()` or `or vector(0)` where silence matters.
- **`roracle_jobs` with several api replicas.** Every replica reports the same database state; aggregate with `max by (kind, status)`, not `sum`.
- **Scrape safety.** The gauges are read with a 2 s statement timeout. If the database is down or slow the scrape still succeeds and simply carries no `roracle_jobs*` series.
- **Oldest queued age** is measured from `run_after`, so a job in retry backoff (its `run_after` is in the future) reads 0 until the backoff ends.
- **`roracle_job_runs_total`.** `retry` and `failed` are also counted when a job is requeued or failed because its lease expired (a crashed worker's job is swept by any worker). `done` is counted once per completed job. A release at shutdown, a deferral (`RequeueLater`) and a lost lease count nothing.
- **`roracle_ingest_stage_failures_total`** counts every exception that leaves an ingest attempt, including ones the queue then retries, a deferral while the per-owner dedupe lock is busy, and a lost lease. Compare with `roracle_job_runs_total{kind="ingest",outcome="failed"}` for permanent failures.
- **Reranker.** Fallbacks only occur when `ROR_RERANK_URL` is set; with it empty no rerank call is made and neither series moves.

## Series that do not exist

Older drafts and dashboards sometimes mention `roracle_search_seconds`, `roracle_chat_ttft_seconds`, `roracle_ingest_seconds` and `roracle_llm_errors_total`. None of them is defined in the code. Use the `route` label of `roracle_http_request_duration_seconds` for search and chat latency, and the age of `roracle_jobs_oldest_queued_seconds{kind="ingest"}` for ingest backlog.

## Not exposed by rOracle

Postgres replication lag, WAL archive age and WAL backlog come from CloudNativePG (`cnpg_*`, and the chart's custom query `cnpg_roracle_archiver_seconds_since_last_archive`). Elasticsearch health has no bundled exporter. See [observability](../enterprise/observability.md).
