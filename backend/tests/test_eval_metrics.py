import math

import pytest

from roracle.eval.gold import GoldItem
from roracle.eval.metrics import Hit, PairResult, aggregate, match_ranks, norm, score_pair

A = GoldItem(doc="a", quote="alpha", grade=3)
B = GoldItem(doc="b", quote="beta", grade=2)
C = GoldItem(doc="c", grade=1)


def h(doc, text="", pages=(1,)):
    return Hit(doc, text, list(pages))


def test_norm_case_whitespace_hyphen():
    assert norm("Be-\nTA   rays\n\tgo") == "beta rays go"
    assert match_ranks([h("b", "the  BE-\nta  wave")], [B]) == [(1, 2)]


def test_quote_page_and_doc_matching_and_credit_once():
    hits = [h("a", "nothing"), h("a", "Alpha!"), h("a", "alpha again"), h("c", "x", [4])]
    assert match_ranks(hits, [A, GoldItem(doc="c", page=4, grade=2), C]) == [(2, 3), (4, 2), (4, 1)]


def test_three_gold_exact_metrics():
    hits = [h("x", "zzz"), h("a", "the ALPHA"), h("c", "any"), h("b", "be-\nta")]
    s = score_pair(hits, [A, B, C])
    dcg = 7 / math.log2(3) + 1 / math.log2(4) + 3 / math.log2(5)
    idcg = 7 / math.log2(2) + 3 / math.log2(3) + 1 / math.log2(4)
    assert s["p_recall@5"] == s["p_recall@10"] == s["p_recall@100"] == 1.0
    assert s["p_mrr"] == 0.5
    assert s["p_ndcg@10"] == pytest.approx(dcg / idcg, abs=1e-12)
    assert set(s) == {
        f"{p}_{m}"
        for p in "pd"
        for m in ("recall@5", "recall@10", "recall@40", "recall@50", "recall@100", "mrr", "ndcg@10")
    }
    assert all(0 <= v <= 1 for v in s.values())


def test_recall_cutoffs():
    hits = [h("x")] * 5 + [h("b", "beta")]
    s = score_pair(hits, [A, B])
    assert (s["p_recall@5"], s["p_recall@10"]) == (0.0, 0.5)
    assert s["p_mrr"] == 1 / 6


def test_doc_level_dedupes_passages():
    hits = [h("a", "alpha"), h("a", "alpha"), h("a", "more"), h("b", "beta")]
    s = score_pair(hits, [A, B, C])
    assert s["d_recall@5"] == 2 / 3  # docs a, b in first two unique slots
    assert s["d_mrr"] == 1.0
    assert s["p_recall@5"] == 2 / 3
    assert s["d_ndcg@10"] > 0


def test_empty_gold_rejected_and_empty_pairs_counted():
    with pytest.raises(ValueError):
        score_pair([], [])
    rows = [
        PairResult("q1", "u1", "fact", {"p_mrr": 1.0}, 0, 0, 10),
        PairResult("q2", "u1", "fact", None, 1, 2, 30),
        PairResult("q1", "u2", "global", {"p_mrr": 0.5}, 0, 1, 20),
    ]
    agg = aggregate(rows)
    assert agg["overall"] == {"p_mrr": 0.75, "pairs": 2}
    assert agg["empty_pairs"] == 1
    assert (agg["acl_violations"], agg["es_acl_violations"]) == (1, 3)
    assert agg["by_kind"]["global"]["p_mrr"] == 0.5
    assert agg["by_principal"]["u1"]["pairs"] == 1
    assert (agg["latency_p50_ms"], agg["latency_p95_ms"]) == (20, 30)


def test_one_passage_credits_every_matching_gold():
    s = score_pair([h("a", "alpha and beta"), h("b", "beta")], [A, GoldItem(doc="a", quote="beta", grade=3)])
    assert s["p_recall@5"] == 1.0 and s["p_mrr"] == 1.0
    assert s["p_ndcg@10"] == 1.0
    s = score_pair(
        [h("a", "alpha and beta")], [GoldItem(doc="a", grade=3), GoldItem(doc="a", page=1, grade=3)]
    )
    assert s["p_recall@5"] == 1.0 and s["p_ndcg@10"] == 1.0
