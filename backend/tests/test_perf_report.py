import pytest

from roracle.perf.explain import seq_scans
from roracle.perf.load import SCENARIOS, build_request
from roracle.perf.report import Report, gate, percentile
from roracle.perf.synth_pg import det_uuid, visibility


def test_percentile_is_nearest_rank():
    xs = list(range(1, 101))
    assert percentile(xs, 50) == 50
    assert percentile(xs, 95) == 95
    assert percentile(xs, 99) == 99
    assert percentile([5.0, 1.0, 3.0], 50) == 3.0  # sorts; ceil(0.5 * 3) = 2nd
    assert percentile([7.0], 99) == 7.0
    assert percentile([], 95) == 0.0


def test_report_counts_errors_in_n():
    r = Report.of([1.0, 2.0, 3.0, 4.0], errors=1)
    assert (r.n, r.errors, r.p50) == (5, 1, 2.0)


def test_gate_fails_above_budget_or_on_errors():
    r = Report.of([10.0] * 98 + [500.0] * 2)
    assert gate(r, 400) is True  # p95 = 10
    assert gate(r, 400, q="p99") is False  # 500 > 400
    assert gate(r, 5) is False
    assert gate(Report.of([1.0], errors=1), 400) is False


def test_seq_scans_finds_nested_nodes():
    plan = {
        "Plan": {
            "Node Type": "Limit",
            "Plans": [
                {"Node Type": "Sort", "Plans": [{"Node Type": "Seq Scan", "Relation Name": "jobs"}]},
                {"Node Type": "Index Scan", "Relation Name": "tags", "Index Name": "ix"},
            ],
        }
    }
    assert seq_scans(plan) == {"jobs"}
    assert seq_scans({"Plan": {"Node Type": "Result"}}) == set()


def test_deterministic_ids_and_mix():
    assert det_uuid(1, "d", 5) == det_uuid(1, "d", 5)
    assert det_uuid(1, "d", 5) != det_uuid(2, "d", 5)
    mix = [visibility(i) for i in range(100)]
    assert (mix.count("everyone"), mix.count("org_unit"), mix.count("private"), mix.count("shared")) == (
        60,
        15,
        15,
        10,
    )


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_scenario_builds_a_deterministic_request(scenario):
    assert build_request(scenario, 3) == build_request(scenario, 3)
    method, path, _ = build_request(scenario, 3)
    assert method in ("GET", "POST") and path.startswith("/")
