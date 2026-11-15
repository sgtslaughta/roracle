import httpx
import pytest

from roracle.config import Settings
from roracle.retrieval import (
    Passage,
    _filters,
    _rerank,
    group_by_document,
    rrf,
    vote_multiplier,
    wilson,
)


def test_rrf_order():
    s = rrf([["a", "b"], ["b", "c"]])
    assert s["b"] > s["a"] > s["c"]
    assert s["a"] == pytest.approx(1 / 61)
    assert s["b"] == pytest.approx(1 / 62 + 1 / 61)


def test_wilson_and_multiplier():
    assert wilson(0, 0) == 0.5
    assert vote_multiplier(0, 0) == 1.0
    assert vote_multiplier(10_000, 0) == pytest.approx(1.15, abs=0.003)
    assert vote_multiplier(0, 10_000) == pytest.approx(0.85, abs=0.003)
    for u, d in [(1, 0), (0, 1), (5, 5), (100, 3)]:
        assert 0.85 <= vote_multiplier(u, d) <= 1.15


def P(c, doc, score, title="T"):
    return Passage(c, doc, "v", "text " * 100, "hl", [1], [], score, title=title)


def test_grouping():
    ps = [P("1", "a", 0.9), P("2", "a", 0.8), P("3", "a", 0.7), P("4", "b", 0.85)]
    hits = group_by_document(ps, {"a": (0, 1000), "b": (1000, 0)})
    assert [h.doc_id for h in hits] == ["b", "a"]
    a = next(h for h in hits if h.doc_id == "a")
    assert [x.chunk_id for x in a.passages] == ["1", "2"]
    assert a.score == pytest.approx(0.9 * vote_multiplier(0, 1000))
    assert hits[0].score == pytest.approx(0.85 * vote_multiplier(1000, 0))


def _settings():
    return Settings(
        database_url="x", s3_endpoint="x", s3_access_key="x", s3_secret_key="x", oidc_issuer="x",
        oidc_jwks_url="x", rerank_url="http://rr/rerank", rerank_model="m",
    )  # fmt: skip


def _http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_rerank_reorders():
    seen = {}

    def h(req):
        seen.update(__import__("json").loads(req.content))
        return httpx.Response(
            200,
            json={"results": [{"index": 0, "relevance_score": 0.1}, {"index": 1, "relevance_score": 0.9}]},
        )

    out = _rerank(_settings(), "q", [P("a", "d", 0.5), P("b", "d", 0.4)], _http(h))
    assert [x.chunk_id for x in out] == ["b", "a"] and out[0].score > out[1].score
    assert seen["model"] == "m" and seen["query"] == "q" and len(seen["documents"]) == 2


def test_rerank_failure_keeps_order():
    ps = [P("a", "d", 0.5), P("b", "d", 0.4)]
    assert _rerank(_settings(), "q", ps, _http(lambda r: httpx.Response(500))) is ps
    assert _rerank(_settings(), "q", ps, _http(lambda r: httpx.Response(200, json={"x": 1}))) is ps


def test_filters_shape():
    from types import SimpleNamespace

    p = SimpleNamespace(user_id="u", is_admin=True, groups=())
    f = _filters(p, {"author": ["A"], "year": [2020, 2021], "tags": []}, ["d1"])
    assert f[1] == {"term": {"is_latest": True}}
    assert f[2:] == [
        {"terms": {"authors": ["A"]}},
        {"terms": {"year": [2020, 2021]}},
        {"terms": {"doc_id": ["d1"]}},
    ]


@pytest.mark.parametrize(
    "resp",
    [
        {"results": [{"index": 0, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.8}]},
        {"results": [{"index": 0.0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.8}]},
        {"results": [{"index": 0, "relevance_score": 0.9}, {"index": 5, "relevance_score": 0.8}]},
        {"results": [{"index": 0, "relevance_score": 0.9}]},
    ],
)
def test_rerank_malformed_keeps_order(resp):
    ps = [P("a", "d", 0.5), P("b", "d", 0.4)]
    assert _rerank(_settings(), "q", ps, _http(lambda r: httpx.Response(200, json=resp))) is ps


def test_rerank_timeout_and_non_json():
    ps = [P("a", "d", 0.5), P("b", "d", 0.4)]

    def boom(req):
        raise httpx.ReadTimeout("t")

    assert _rerank(_settings(), "q", ps, _http(boom)) is ps
    assert _rerank(_settings(), "q", ps, _http(lambda r: httpx.Response(200, content=b"nope"))) is ps
