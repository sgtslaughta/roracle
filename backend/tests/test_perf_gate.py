"""Budget gate semantics (Task 10.13) and the load-test warm-up (parked 10.1 minor 5)."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from roracle.perf import load
from roracle.perf.report import check


def test_latency_budget_scales_with_the_profile_factor():
    spec = {"p95": 400}
    assert check(spec, {"p95": 390})[0]
    assert not check(spec, {"p95": 410})[0]
    assert check(spec, {"p95": 590}, factor=1.5)[0] and not check(spec, {"p95": 610}, factor=1.5)[0]


def test_min_budget_is_divided_and_max_zero_stays_zero():
    assert check({"min": 1000}, {"value": 700}, factor=1.5)[0]  # 1000 / 1.5 = 666
    assert not check({"min": 1000}, {"value": 600}, factor=1.5)[0]
    assert check({"max": 0}, {"value": 0}, factor=3)[0]
    assert not check({"max": 0}, {"value": 1}, factor=3)[0]  # no loosening for the no-write invariant


def test_errors_and_missing_samples_fail_the_gate():
    assert not check({"p95": 400}, {"p95": 1, "errors": 2})[0]
    assert not check({"p99": 5}, {"p95": 1})[0]


def test_warmup_requests_are_sent_but_not_measured():
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = load.run(
            f"http://127.0.0.1:{srv.server_port}",
            ["t"],
            "search",
            rps=20,
            duration_s=1,
            concurrency=4,
            warmup_s=1,
        )
    finally:
        srv.shutdown()
    assert len(hits) == 40  # 1 s warm-up + 1 s measured
    assert r.n == 20  # only the measured second is in the report


def test_doc_read_scenario_only_asks_for_documents_everybody_can_read():
    from roracle.perf.synth_pg import det_uuid, visibility

    docs = 1000
    everyone = {str(det_uuid(1, "d", i)) for i in range(docs) if visibility(i) == "everyone"}
    for n in range(300):
        _, path, _ = load.build_request("doc_read", n, docs=docs)
        assert path.rsplit("/", 1)[1] in everyone


def test_scratch_database_guard_is_an_exact_pattern():
    from roracle.perf.cli import SCRATCH_DB

    for ok in ("roracle_perf", "roracle_perfci", "roracle_perf_2"):
        assert SCRATCH_DB.fullmatch(ok)
    for bad in (
        "roracle",
        "roracle_perf_prod-x",
        "prod_roracle_perf",
        "perf",
        "roracle_perf;drop",
        "my_roracle_perf",
    ):
        assert not SCRATCH_DB.fullmatch(bad)
