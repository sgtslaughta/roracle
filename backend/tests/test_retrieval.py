from types import SimpleNamespace

import httpx
import pytest

from roracle.config import Settings
from roracle.labels.policy import Policy
from roracle.retrieval import (
    Passage,
    _filters,
    _rerank,
    group_by_document,
    rrf,
    vote_multiplier,
)


def test_rrf_order():
    s = rrf([["a", "b"], ["b", "c"]])
    assert s["b"] > s["a"] > s["c"]
    assert s["a"] == pytest.approx(1 / 61)
    assert s["b"] == pytest.approx(1 / 62 + 1 / 61)


def test_vote_multiplier():
    assert vote_multiplier(0, 0) == 1.0
    assert vote_multiplier(1, 0) == pytest.approx(1.05) and vote_multiplier(1, 0) > 1
    assert vote_multiplier(0, 1) == pytest.approx(0.95) and vote_multiplier(0, 1) < 1
    assert 1.14 < vote_multiplier(1000, 0) < 1.15
    assert 0.85 < vote_multiplier(0, 1000) < 0.86
    for u in range(6):
        for d in range(6):
            assert vote_multiplier(u + 1, d) > vote_multiplier(u, d)
            assert vote_multiplier(u, d + 1) < vote_multiplier(u, d)


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

    p = SimpleNamespace(user_id="u", is_admin=True, groups=(), via="jwt")
    f = _filters(p, Policy([]), {"author": ["A"], "year": [2020, 2021], "tags": []}, ["d1"], False)
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


def _knn_calls(**kw):
    from types import SimpleNamespace

    from roracle.auth import Principal
    from roracle.retrieval import search_chunks

    calls = []

    class ES:
        def search(self, **k):
            calls.append(k)
            return {"hits": {"hits": []}}

    llm = SimpleNamespace(
        embeddings=SimpleNamespace(
            create=lambda model, input: SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1])])
        )
    )
    import uuid

    p = Principal(uuid.uuid4(), "s", "u", (), False)
    s = Settings(embed_dims=1)
    search_chunks(ES(), llm, s, p, "q", policy=Policy([]), filters={}, **kw)
    return [c["knn"] for c in calls if "knn" in c]


def _knn_of(**kw):
    return _knn_calls(**kw)[0]


def test_min_similarity_goes_to_knn():
    assert _knn_of(min_similarity=0.4)["similarity"] == 0.4
    assert "similarity" not in _knn_of()
    assert "similarity" not in _knn_of(min_similarity=0.0)


def test_summary_knn_shares_floor_and_can_be_skipped():
    both = _knn_calls(min_similarity=0.4)
    assert [k["field"] for k in both] == ["embedding", "summary_embedding"]
    assert {k["similarity"] for k in both} == {0.4}
    assert [k["field"] for k in _knn_calls(summary_knn=False)] == ["embedding"]


def test_chat_settings():
    s = Settings(chat_models=" a:1, ,b:2 ")
    assert s.chat_model_list == ["a:1", "b:2"]
    assert Settings().chat_min_similarity == 0.35 and Settings().chat_passages == 8


def _run_search(es):
    from types import SimpleNamespace

    from roracle.auth import Principal
    from roracle.retrieval import search_chunks

    llm = SimpleNamespace(
        embeddings=SimpleNamespace(
            create=lambda model, input: SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.0] * 4)])
        )
    )
    s = _settings().model_copy(update={"rerank_url": "", "embed_dims": 4})
    p = Principal(user_id=None, sub="s", username="u", groups=(), is_admin=True)
    return search_chunks(es, llm, s, p, "q", policy=Policy([]), filters={})


def _hit(c):
    return {"_source": {"chunk_id": c, "doc_id": "d" + c, "version_id": "v", "text": "t"}}


class _ES:
    def __init__(self, summary_error=False):
        self.calls = []
        self.summary_error = summary_error

    def search(self, **kw):
        from elasticsearch import BadRequestError

        self.calls.append(kw)
        knn = kw.get("knn")
        if knn is None:
            return {"hits": {"hits": [_hit("a")]}}
        if knn["field"] == "summary_embedding":
            if self.summary_error:
                raise BadRequestError("x", SimpleNamespace(status=400), {})
            return {"hits": {"hits": [_hit("s")]}}
        return {"hits": {"hits": [_hit("a")]}}


def test_search_chunks_summary_knn_third_list():
    es = _ES()
    out = _run_search(es)
    assert [x.chunk_id for x in out] == ["a", "s"]
    assert out[1].score == pytest.approx(1 / 61)
    bm25, knn, sknn = es.calls
    assert sknn["knn"]["filter"] == knn["knn"]["filter"] == bm25["query"]["bool"]["filter"]


def test_search_chunks_tolerates_index_without_summary_field():
    out = _run_search(_ES(summary_error=True))
    assert [x.chunk_id for x in out] == ["a"]
