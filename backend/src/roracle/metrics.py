"""Prometheus metrics (R3). HTTP + job/ingest counters live in the default registry (api and worker);
queue-depth gauges are collected at scrape time from Postgres and served by the API only."""

import time

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import text

HTTP_REQUESTS = Counter("roracle_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_DURATION = Histogram("roracle_http_request_duration_seconds", "HTTP latency", ["method", "route"])
JOB_RUNS = Counter("roracle_job_runs_total", "Job runs by outcome (done|retry|failed)", ["kind", "outcome"])
NEARDUP_EXHAUSTIVE = Counter(
    "roracle_neardup_exhaustive_total",
    "Near-dup checks that fell back to the exhaustive scan because of an operational problem",
    ["reason"],
)
INGEST_STAGE_FAILURES = Counter(
    "roracle_ingest_stage_failures_total", "Ingest failures by the stage being advanced", ["stage"]
)
RERANK_SECONDS = Histogram(
    "roracle_rerank_seconds",
    "Reranker call latency",
    buckets=(0.05, 0.1, 0.2, 0.4, 0.8, 1.5, 3, 6),
)
RERANK_FALLBACK = Counter(
    "roracle_rerank_fallback_total", "Rerank failures that kept the fused order", ["reason"]
)

AUDIT_WRITE_FAILURES = Counter(
    "roracle_audit_write_failures_total", "Audit rows that could not be written", ["path"]
)

_DEPTH_SQL = text(
    "SELECT kind, status, count(*), "
    "COALESCE(EXTRACT(EPOCH FROM now() - min(run_after) FILTER (WHERE status = 'queued')), 0) "
    "FROM jobs WHERE status IN ('queued', 'running', 'failed') GROUP BY kind, status"
)


class JobsCollector:
    """roracle_jobs{kind,status} and roracle_jobs_oldest_queued_seconds{kind}; 2 s statement timeout."""

    def __init__(self, session_factory):
        self.sf = session_factory

    def collect(self):
        jobs = GaugeMetricFamily("roracle_jobs", "Jobs by kind and status", labels=["kind", "status"])
        oldest = GaugeMetricFamily(
            "roracle_jobs_oldest_queued_seconds", "Age of the oldest queued job", labels=["kind"]
        )
        try:
            with self.sf() as s:
                s.execute(text("SET LOCAL statement_timeout = 2000"))
                rows = s.execute(_DEPTH_SQL).all()
        except Exception:  # noqa: BLE001 scrape must not fail because the DB is down
            rows = []
        for kind, status, n, age in rows:
            jobs.add_metric([kind, status], n)
            if status == "queued":
                oldest.add_metric([kind], max(float(age), 0.0))
        yield jobs
        yield oldest


def render(session_factory=None) -> bytes:
    out = generate_latest()
    if session_factory is not None:
        reg = CollectorRegistry()
        reg.register(JobsCollector(session_factory))
        out += generate_latest(reg)
    return out


async def http_middleware(request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = getattr(request.scope.get("route"), "path", None) or "unmatched"
        if route != "/metrics":
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_DURATION.labels(request.method, route).observe(time.perf_counter() - start)
