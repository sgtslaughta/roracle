import json
import random
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from roracle import reembed
from roracle.eval import gate, report
from roracle.eval.stats import paired_bootstrap
from roracle.eval.store import PairView, RunView
from roracle.models import Job
from roracle.models_eval import EvalSet

PRINCIPALS = {"alice": False, "bob": False, "root": True}


def mkrun(
    n=200,
    *,
    path="chat",
    ref=True,
    ndcg=0.5,
    r10=0.6,
    r100=0.9,
    faith=0.9,
    cite=0.9,
    acl=0,
    es_acl=0,
    p95=100,
    noise=None,
    d40=None,
    pri=PRINCIPALS,
    judge=True,
):
    """Synthetic run: constant metrics plus optional per-query noise `noise(i) -> float` added to ndcg/r10."""
    pairs = []
    for i in range(n):
        for pr in pri:
            d = noise(i) if noise else 0.0
            scores = {
                "p_ndcg@10": ndcg + d,
                "p_recall@10": r10 + d,
                "p_recall@100": r100,
                "p_mrr": 0.5,
                "d_recall@10": r10 + d,
                "d_recall@40": 0.9 + (d40(i) if d40 else 0.0),
            }
            j = {"faithfulness": faith, "citation_support": cite} if judge else None
            pairs.append(PairView(f"q{i}", pr, ["fact", "multihop", "global"][i % 3], scores, j))
    m = {
        "overall": {"pairs": len(pairs)},
        "by_kind": {},
        "acl_violations": acl,
        "es_acl_violations": es_acl,
        "empty_pairs": 0,
        "latency_p50_ms": 50,
        "latency_p95_ms": p95,
    }
    return RunView(
        str(uuid.uuid4()),
        "ref",
        path,
        "run",
        {"principals": pri, "overrides": {}, "is_reference": ref},
        m,
        pairs,
    )


def test_bootstrap_deterministic_and_paired():
    rng = random.Random(1)
    a = [rng.random() for _ in range(100)]
    b = [x + 0.05 for x in a]
    r1, r2 = paired_bootstrap(a, b), paired_bootstrap(a, b)
    assert (
        r1 == r2
        and r1[0] == pytest.approx(0.05)
        and r1[1] == pytest.approx(0.05)
        and r1[2] == pytest.approx(0.05)
    )
    assert paired_bootstrap(a, b, seed=1) != paired_bootstrap(a, [x + rng.random() * 0.1 for x in a], seed=1)
    assert paired_bootstrap([], []) == (0.0, 0.0, 0.0)
    with pytest.raises(ValueError):
        paired_bootstrap([1.0], [])


def test_bootstrap_noisy_interval_straddles_zero():
    rng = random.Random(2)
    a = [rng.random() for _ in range(50)]
    b = [rng.random() for _ in range(50)]
    d, lo, hi = paired_bootstrap(a, b)
    assert lo < d < hi and lo < 0 < hi


def test_pass_on_clear_gain():
    v = gate.verdict(mkrun(), mkrun(ndcg=0.55, r10=0.6, r100=0.9))
    assert v.status == "PASS", v.reasons
    assert v.deltas["p_ndcg@10"]["delta"] == pytest.approx(0.05) and v.deltas["p_ndcg@10"]["lo"] > 0


def test_recall10_gain_alone_is_enough():
    assert gate.verdict(mkrun(), mkrun(r10=0.65)).status == "PASS"


def test_fail_small_gain_and_noisy_gain():
    v = gate.verdict(mkrun(), mkrun(ndcg=0.51))
    assert v.status == "FAIL" and any("no gain" in r for r in v.reasons)
    rng = random.Random(3)
    noisy = [rng.uniform(-2, 2) for _ in range(200)]  # mean gain >= 0.02 but CI includes 0
    base, cand = mkrun(), mkrun(ndcg=0.5, r10=0.6, noise=lambda i: noisy[i] + 0.03)
    d = gate.metric_deltas(base, cand)["p_ndcg@10"]
    assert d["lo"] <= 0 < d["hi"]
    assert gate.verdict(base, cand).status == "FAIL"


def test_fail_recall100_loss():
    assert gate.verdict(mkrun(), mkrun(ndcg=0.6, r100=0.894)).status == "FAIL"
    assert gate.verdict(mkrun(), mkrun(ndcg=0.6, r100=0.896)).status == "PASS"  # within 0.005


def test_fail_judge_loss_and_ok_within_tolerance():
    v = gate.verdict(mkrun(), mkrun(ndcg=0.6, faith=0.85))
    assert v.status == "FAIL" and any("faithfulness" in r for r in v.reasons)
    assert gate.verdict(mkrun(), mkrun(ndcg=0.6, cite=0.89)).status == "PASS"


@pytest.mark.parametrize("kw", [{"acl": 1}, {"es_acl": 2}])
def test_fail_on_acl_violation_even_on_tiny_set(kw):
    v = gate.verdict(mkrun(10), mkrun(10, ndcg=0.9, **kw))
    assert v.status == "FAIL" and any("acl" in r for r in v.reasons)


def test_insufficient_when_too_few_queries_or_principals():
    v = gate.verdict(mkrun(199), mkrun(199, ndcg=0.6))
    assert v.status == "INSUFFICIENT" and "199 all queries" in v.reasons[0]
    two = {"alice": False, "root": True}
    assert gate.verdict(mkrun(pri=two), mkrun(ndcg=0.6, pri=two)).status == "INSUFFICIENT"
    admins = {"a": True, "b": True, "c": True}
    assert gate.verdict(mkrun(pri=admins), mkrun(ndcg=0.6, pri=admins)).status == "INSUFFICIENT"
    assert gate.verdict(mkrun(199), mkrun(199, ndcg=0.5)).status == "INSUFFICIENT"  # missing gain, small set


def test_missing_judge_is_insufficient_not_pass():
    v = gate.verdict(mkrun(judge=False), mkrun(ndcg=0.6, judge=False))
    assert v.status == "INSUFFICIENT" and any("--judge" in r for r in v.reasons)


def test_search_path_needs_no_judge_and_can_pass():
    base, cand = mkrun(path="search", judge=False), mkrun(path="search", ndcg=0.6, judge=False)
    assert gate.verdict(base, cand).status == "PASS"
    v = gate.verdict(mkrun(judge=False), mkrun(path="search", ndcg=0.6, judge=False))  # mixed paths
    assert v.status == "INSUFFICIENT" and any("differ in set or path" in r for r in v.reasons)


def test_only_the_reference_set_can_pass():
    v = gate.verdict(mkrun(ref=False), mkrun(ndcg=0.6, ref=False))
    assert v.status == "INSUFFICIENT" and any("reference set" in r for r in v.reasons)


def test_latency_budget():
    assert gate.verdict(mkrun(), mkrun(ndcg=0.6, p95=300), latency_budget_ms=200).status == "FAIL"
    assert gate.verdict(mkrun(), mkrun(ndcg=0.6, p95=150), latency_budget_ms=200).status == "PASS"


def test_scope_uses_only_that_kind():
    base = mkrun(700)
    cand = mkrun(700, ndcg=0.5, noise=lambda i: 0.1 if i % 3 == 2 else 0.0)  # only "global" queries improve
    assert gate.verdict(base, cand, scope="global").status == "PASS"
    assert gate.verdict(base, cand, scope="fact").status == "FAIL"  # no gain on fact queries
    assert gate.verdict(base, cand).status == "PASS"  # +0.033 over all kinds
    g = lambda n: (mkrun(n), mkrun(n, noise=lambda i: 0.1 if i % 3 == 2 else 0.0, ndcg=0.5))
    assert gate.verdict(*g(300), scope="global").status == "PASS"  # 100 global queries >= 50, set >= 200
    assert gate.metric_deltas(base, cand, "global")["p_ndcg@10"]["delta"] == pytest.approx(0.1)
    assert gate.metric_deltas(base, cand, "fact")["p_ndcg@10"]["delta"] == pytest.approx(0.0)


def test_markdown_has_verdict_deltas_and_config_diff():
    base, cand = mkrun(), mkrun(ndcg=0.6)
    base.config["overrides"] = {"rerank_depth": 50}
    cand.config["overrides"] = {"rerank_depth": 200}
    v = gate.verdict(base, cand)
    md = report.markdown(base, cand, v)
    assert f"**Verdict: {v.status}**" in md
    assert "| p_ndcg@10 | 0.5000 | 0.6000 | +0.1000 |" in md
    assert "| rerank_depth | 50 | 200 |" in md and "## By kind" in md and "acl_violations" in md
    j = report.json(cand)
    assert j["id"] == cand.id and j["pairs"] == len(cand.pairs) and json.dumps(j)


# ---- cutover enqueues eval_run only with a reference set -------------------------------------------
def _cutover(db, settings, **kw):
    payload = {"target": {"model": "new", "dims": 3, "index": "chunks-new-1"}, "source": "chunks-old-1"}
    from types import SimpleNamespace

    es = SimpleNamespace(
        indices=SimpleNamespace(
            exists_alias=lambda **k: True,
            update_aliases=lambda **k: None,
            get_alias=lambda **k: {"chunks-new-1": {}},
        )
    )
    reembed._cutover(db, payload, es, settings.model_copy(update=kw), None)
    return list(db.scalars(select(Job).where(Job.kind == "eval_run")))


def test_eval_runs_after_catchup_round_two(db_session, settings, monkeypatch):
    monkeypatch.setattr(reembed, "clear_summary_embeddings", lambda sf: None)
    db_session.add(EvalSet(name="ref", is_reference=True))
    db_session.flush()
    (ev,) = _cutover(db_session, settings)
    catch = db_session.scalars(select(Job).where(Job.kind == "reembed")).all()
    assert len(catch) == 1  # catch-up round 1 at +1x; round 2 is enqueued by it, at >= +2x
    db_session.refresh(ev)
    db_session.refresh(catch[0])
    assert ev.run_after - catch[0].run_after >= timedelta(seconds=2 * reembed.CATCHUP_DELAY_S)


def test_cutover_enqueues_eval_run_only_with_reference_set(db_session, settings, monkeypatch):
    monkeypatch.setattr(reembed, "clear_summary_embeddings", lambda sf: None)
    assert _cutover(db_session, settings) == []  # no reference set
    ref = EvalSet(name="ref", is_reference=True)
    db_session.add(ref)
    db_session.flush()
    assert _cutover(db_session, settings, eval_on_reindex=False) == []
    (job,) = _cutover(db_session, settings)
    assert job.payload == {"set_id": str(ref.id), "trigger": "reindex", "judge": True}


def test_per_scope_minimum_is_50_and_whole_set_200():
    """Ledger M3: a scope needs >= 50 queries AND the reference set as a whole >= 200."""
    assert gate.MIN_SCOPE_QUERIES == 50 and gate.MIN_QUERIES == 200
    noisy = lambda n: (mkrun(n), mkrun(n, noise=lambda i: 0.1 if i % 3 == 2 else 0.0, ndcg=0.5))
    v = gate.verdict(*noisy(300 - 3), scope="global")  # 99 global queries
    assert v.status == "PASS"
    v = gate.verdict(*noisy(120), scope="global")  # 40 global queries, set too small too
    assert v.status == "INSUFFICIENT" and any("need 50" in r for r in v.reasons)
    v = gate.verdict(*noisy(149), scope="global")  # 49 global queries, 149 total
    assert any("need 50" in r for r in v.reasons) and any("need 200" in r for r in v.reasons)
    v = gate.verdict(*noisy(199), scope="global")  # 66 global but the whole set is 199 < 200
    assert v.status == "INSUFFICIENT" and not any("need 50" in r for r in v.reasons)


def test_global_scope_uses_doc_recall_and_guards_fact():
    base = mkrun(300)
    cand = mkrun(300, noise=lambda i: 0.06 if i % 3 == 2 else 0.0)
    v = gate.verdict(base, cand, scope="global")
    assert v.status == "PASS" and "d_recall@10" in v.reasons[0]
    weak = mkrun(300, noise=lambda i: 0.04 if i % 3 == 2 else 0.0)
    assert gate.verdict(base, weak, scope="global").status == "FAIL"  # +0.04 < 0.05 doc-recall gain
    hurt = mkrun(300, noise=lambda i: 0.06 if i % 3 == 2 else (-0.01 if i % 3 == 0 else 0.0))
    v = gate.verdict(base, hurt, scope="global")  # fact ndcg -0.01 < -0.005
    assert v.status == "FAIL" and any("fact p_ndcg@10" in r for r in v.reasons)


def test_global_scope_guards_doc_recall_40_not_passage_recall_100():
    """Ledger I7: the global candidate list is capped at 40 documents, so p_recall@100 does not apply to it."""
    base = mkrun(300)
    gain = lambda i: 0.06 if i % 3 == 2 else 0.0
    low100 = mkrun(300, noise=gain, r100=0.5)  # passage recall@100 collapses; doc recall@40 holds
    assert gate.verdict(base, low100, scope="global").status == "PASS"
    assert gate.verdict(base, low100, scope="all").status == "FAIL"  # other scopes still guard p_recall@100
    lost = mkrun(300, noise=gain, d40=lambda i: -0.1 if i % 3 == 2 else 0.0)
    v = gate.verdict(base, lost, scope="global")
    assert v.status == "FAIL" and any("d_recall@40" in r for r in v.reasons)
