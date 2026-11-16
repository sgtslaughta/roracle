import os
import urllib.error
import urllib.request

from sqlalchemy import text

from roracle import metrics, worker_health
from roracle.queue import NonRetryable, enqueue, run_once


def _checks(**kw):
    ok = {"db": lambda: True, "es": lambda: True, "s3": lambda: True}
    return ok | kw


def test_readyz_ok(client, app):
    app.state.ready_checks = _checks()
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"checks": {"db": "ok", "es": "ok", "s3": "ok"}}


def test_readyz_503_hides_error_text(client, app):
    def boom():
        raise RuntimeError("secret-password-123 at http://es:9200")

    app.state.ready_checks = _checks(es=boom, s3=lambda: False)
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json() == {"checks": {"db": "ok", "es": "fail", "s3": "fail"}}
    assert "secret" not in r.text


def test_readyz_timeout_is_fail(client, app, monkeypatch):
    import time

    from roracle.api.routes import health

    monkeypatch.setattr(health, "CHECK_TIMEOUT_S", 0.05)
    app.state.ready_checks = _checks(db=lambda: time.sleep(0.5))
    assert client.get("/readyz").json()["checks"]["db"] == "fail"


def test_metrics_families_and_route_template(client, app, db_session):
    app.state.ready_checks = _checks()
    client.get("/documents/00000000-0000-0000-0000-000000000000")  # 401, but routed
    body = client.get("/metrics").text
    assert 'roracle_http_requests_total{method="GET",route="/documents/{doc_id}",status="401"}' in body
    assert "roracle_http_request_duration_seconds_bucket" in body
    assert "# TYPE roracle_jobs_oldest_queued_seconds gauge" in body
    assert "roracle_job_runs_total" in body
    assert "roracle_ingest_stage_failures_total" in body


def test_jobs_gauges_from_db(session_factory):
    with session_factory() as s:
        s.execute(text("DELETE FROM jobs"))
        enqueue(s, "t_depth", {})
        s.commit()
    try:
        body = metrics.render(session_factory).decode()
        assert 'roracle_jobs{kind="t_depth",status="queued"} 1.0' in body
        assert 'roracle_jobs_oldest_queued_seconds{kind="t_depth"}' in body
    finally:
        with session_factory() as s:
            s.execute(text("DELETE FROM jobs"))
            s.commit()


def test_job_run_counters(session_factory):
    def val(kind, outcome):
        return metrics.JOB_RUNS.labels(kind, outcome)._value.get()

    with session_factory() as s:
        s.execute(text("DELETE FROM jobs"))
        enqueue(s, "t_ok", {})
        enqueue(s, "t_bad", {})
        enqueue(s, "t_perm", {})
        s.commit()

    def bad(*_):
        raise RuntimeError("x")

    def perm(*_):
        raise NonRetryable("x")

    before = {(k, o): val(k, o) for k, o in [("t_ok", "done"), ("t_bad", "retry"), ("t_perm", "failed")]}
    handlers = {"t_ok": lambda *_: None, "t_bad": bad, "t_perm": perm}
    while run_once(session_factory, handlers):
        pass
    for (k, o), v in before.items():
        assert val(k, o) == v + 1
    with session_factory() as s:
        s.execute(text("DELETE FROM jobs WHERE kind LIKE 't\\_%'"))
        s.commit()


def _get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_worker_health_server_stale_heartbeat():
    srv = worker_health.serve(18991)
    try:
        worker_health.beat()
        assert _get(18991, "/healthz")[0] == 200
        worker_health._beat -= worker_health.STALE_S + 1
        assert _get(18991, "/healthz")[0] == 503
        worker_health.working = True  # a long-running job keeps the worker healthy
        assert _get(18991, "/healthz")[0] == 200
        worker_health.working = False
        worker_health.beat()
        status, body = _get(18991, "/metrics")
        assert status == 200 and "roracle_job_runs_total" in body
    finally:
        srv.shutdown()
        srv.server_close()


def test_worker_health_off_when_port_zero():
    assert worker_health.serve(0) is None


def test_otel_exec_wrapper(monkeypatch):
    from roracle import __main__ as m

    calls = []
    monkeypatch.setattr(os, "execvp", lambda f, a: calls.append((f, a)))
    monkeypatch.setattr(m.shutil, "which", lambda n: "/usr/bin/opentelemetry-instrument")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://c:4318")
    monkeypatch.delenv("ROR_OTEL_WRAPPED", raising=False)
    m.maybe_wrap_otel(["worker"])
    assert calls[0][0] == "opentelemetry-instrument"
    assert calls[0][1][0] == "opentelemetry-instrument" and calls[0][1][-1] == "worker"
    assert os.environ["ROR_OTEL_WRAPPED"] == "1"
    calls.clear()
    m.maybe_wrap_otel(["worker"])  # already wrapped: no loop
    assert not calls


def test_otel_not_wrapped_without_endpoint_or_binary(monkeypatch):
    from roracle import __main__ as m

    calls = []
    monkeypatch.setattr(os, "execvp", lambda f, a: calls.append(1))
    monkeypatch.delenv("ROR_OTEL_WRAPPED", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    m.maybe_wrap_otel(["api"])
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://c:4318")
    monkeypatch.setattr(m.shutil, "which", lambda n: None)
    m.maybe_wrap_otel(["api"])
    m.maybe_wrap_otel(["migrate"])
    assert not calls


def test_db_connect_timeout_bounded(monkeypatch):
    from roracle import db

    seen = {}
    monkeypatch.setattr(db, "create_engine", lambda url, **kw: seen.update(kw))
    db.make_session_factory("postgresql+psycopg://x/y")
    assert seen["connect_args"]["connect_timeout"] <= 3  # bounds /readyz DB check and the metrics collector


def test_readyz_s3_client_has_short_timeouts(app):
    from roracle.api.routes import health

    checks = health._default_checks(app)
    assert "s3" in checks
    cfg = health.s3_check_config()
    assert cfg.connect_timeout <= 3 and cfg.read_timeout <= 3 and cfg.retries["max_attempts"] == 1
