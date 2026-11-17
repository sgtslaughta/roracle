from types import SimpleNamespace

import httpx
import pytest
from es_fakes import msearch as _es_msearch

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
        oidc_jwks_url="x", rerank_url="http://rr/rerank", rerank_model="m", rerank_blend=1.0,
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
    assert "unit" in str(f[2])  # chunks only unless units are requested
    assert f[3:] == [
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
        msearch = _es_msearch

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

    msearch = _es_msearch

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


def _tei():
    return _settings().model_copy(update={"rerank_api": "tei", "rerank_api_key": "sekret"})


def test_tei_request_texts_and_list_response_reorders():
    import json

    seen = {}

    def h(req):
        seen.update(json.loads(req.content))
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json=[{"index": 1, "score": 0.9}, {"index": 0, "score": 0.1}])

    out = _rerank(_tei(), "q", [P("a", "d", 0.5), P("b", "d", 0.4)], _http(h))
    assert [x.chunk_id for x in out] == ["b", "a"]
    assert "documents" not in seen and len(seen["texts"]) == 2 and seen["truncate"] is True
    assert seen["auth"] == "Bearer sekret"


def test_cohere_has_no_auth_without_key_and_pure_helpers():
    from roracle.retrieval import _rerank_order, _rerank_request

    s = _settings()
    assert _rerank_request(s, "q", ["x"]) == {"model": "m", "query": "q", "documents": ["x"]}
    assert _rerank_order(
        s, {"results": [{"index": 0, "relevance_score": 1}, {"index": 1, "relevance_score": 2}]}
    ) == [1, 0]
    assert _rerank_order(_tei(), [{"index": 0, "score": 1}, {"index": 1, "score": 2}]) == [1, 0]
    seen = {}

    def h(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"results": []})

    _rerank(s, "q", [P("a", "d", 0.5)], _http(h))
    assert seen["auth"] is None


def _fallback(reason):
    from roracle.metrics import RERANK_FALLBACK

    return RERANK_FALLBACK.labels(reason)._value.get()


def test_fallback_reasons_counted():
    ps = [P("a", "d", 0.5), P("b", "d", 0.4)]

    def boom(req):
        raise httpx.ReadTimeout("slow")

    for reason, h in (
        ("timeout", boom),
        ("http", lambda r: httpx.Response(503)),
        ("malformed", lambda r: httpx.Response(200, json={"x": 1})),
    ):
        before = _fallback(reason)
        assert _rerank(_tei(), "q", ps, _http(h)) is ps
        assert _fallback(reason) == before + 1


def test_texts_truncated_and_depth_200_sends_200():
    import json

    seen = {}

    def h(req):
        seen.update(json.loads(req.content))
        n = len(seen["texts"])
        return httpx.Response(200, json=[{"index": i, "score": 1 - i / n} for i in range(n)])

    s = _tei().model_copy(update={"rerank_max_chars": 5})
    ps = [P(f"c{i}", "d", 1 - i / 300) for i in range(250)]
    for x in ps:
        x.text = "x" * 50
    out = _rerank(s, "q", ps, _http(h))
    assert (
        Settings().rerank_depth == 200 and len(seen["texts"]) == 200 and set(seen["texts"]) == {"T\nxxxxx"}
    )  # title + newline + truncated text
    assert len(out) == 250


def test_chat_search_uses_chat_candidates(monkeypatch):
    from roracle.chat import retrieve

    sizes = []
    monkeypatch.setattr(retrieve, "search_chunks", lambda *a, **k: sizes.append(k["size"]) or [])
    monkeypatch.setattr(retrieve.Policy, "load", staticmethod(lambda s: None))
    retrieve._search(None, None, None, _settings(), None, "q", None, _settings().chat_candidates, [0.0])
    assert sizes == [200] and Settings().chat_candidates == 200


def test_nonfinite_scores_fall_back_malformed():
    ps = [P("a", "d", 0.5), P("b", "d", 0.4)]
    before = _fallback("malformed")
    for sc in ("NaN", "Infinity"):
        body = f'[{{"index":0,"score":{sc}}},{{"index":1,"score":0.2}}]'
        assert _rerank(_tei(), "q", ps, _http(lambda r, b=body: httpx.Response(200, content=b))) is ps
    assert _fallback("malformed") == before + 2


def test_chunked_requests_merge_scores_globally():
    import json

    calls = []

    def h(req):
        texts = json.loads(req.content)["texts"]
        calls.append(len(texts))
        # score = text number: later passages win, across chunk boundaries
        return httpx.Response(
            200, json=[{"index": i, "score": float(t.split()[-1])} for i, t in enumerate(texts)]
        )

    s = _tei().model_copy(update={"rerank_max_batch": 100, "rerank_depth": 250})
    ps = [P(f"c{i}", "d", 1 - i / 1000) for i in range(300)]
    for i, x in enumerate(ps):
        x.text = f"n {i}"
    out = _rerank(s, "q", ps, _http(h))
    assert calls == [100, 100, 50]
    assert [x.chunk_id for x in out[:3]] == ["c249", "c248", "c247"] and out[-1].chunk_id == "c299"


def test_total_budget_shared_across_chunks():
    import time as _t

    seen = []

    def h(req):
        seen.append(req.extensions["timeout"]["read"])
        _t.sleep(0.15)
        n = len(__import__("json").loads(req.content)["texts"])
        return httpx.Response(200, json=[{"index": i, "score": 1.0 - i} for i in range(n)])

    s = _tei().model_copy(update={"rerank_max_batch": 1, "rerank_timeout_s": 0.2})
    ps = [P("a", "d", 0.5), P("b", "d", 0.4), P("c", "d", 0.3)]
    before = _fallback("timeout")
    assert _rerank(s, "q", ps, _http(h)) is ps
    assert seen[0] <= 0.2 and seen[1] < seen[0] and _fallback("timeout") == before + 1


def test_api_key_never_logged(caplog):
    ps = [P("a", "d", 0.5)]

    def boom(req):
        raise httpx.ConnectError("refused", request=req)

    with caplog.at_level("DEBUG"):
        assert _rerank(_tei(), "q", ps, _http(boom)) is ps
        assert _rerank(_tei(), "q", ps, _http(lambda r: httpx.Response(401))) is ps
    assert "sekret" not in caplog.text and "Bearer" not in caplog.text
    assert "401" in caplog.text


def test_chat_candidates_bounds():
    with pytest.raises(ValueError):
        Settings(chat_candidates=0)
    with pytest.raises(ValueError):
        Settings(chat_candidates=501)


def test_blend_default_is_03_and_reranker_stays_off():
    s = Settings(
        database_url="x",
        s3_endpoint="x",
        s3_access_key="x",
        s3_secret_key="x",
        oidc_issuer="x",
        oidc_jwks_url="x",
    )
    assert s.rerank_blend == 0.3 and s.rerank_url == "" and s.search_rerank_depth == 50


def test_blend_min_max_per_query_and_replace_at_one():
    from roracle.retrieval import blend_order

    fused, rr = [0.030, 0.020, 0.010], [0.1, 0.9, 0.5]  # first stage prefers a, reranker prefers b
    assert blend_order(fused, rr, 1.0) == [1, 2, 0]  # replace
    assert blend_order(fused, rr, 0.0) == [0, 1, 2]  # first stage only
    # 0.3: a = .7*1 + .3*0 = .7 ; b = .7*.5 + .3*1 = .65 ; c = 0 + .3*.5 = .15
    assert blend_order(fused, rr, 0.3) == [0, 1, 2]
    assert blend_order(fused, [0.0, 1.0, 0.5], 0.5) == [1, 0, 2]  # a=.5 b=.5+0.25=.75... reranker lifts b
    assert blend_order([1.0, 1.0], [0.2, 0.8], 0.3) == [1, 0]  # constant first stage: reranker decides


def test_rerank_blend_reorders_and_sends_title_and_text():
    seen = {}

    def h(req):
        seen.update(__import__("json").loads(req.content))
        return httpx.Response(
            200,
            json={"results": [{"index": 0, "relevance_score": 0.1}, {"index": 1, "relevance_score": 0.9}]},
        )

    s = _settings().model_copy(update={"rerank_blend": 0.3})
    out = _rerank(s, "q", [P("a", "d", 0.5, title="Alpha"), P("b", "d", 0.4, title="")], _http(h))
    assert seen["documents"][0].startswith("Alpha\ntext ") and seen["documents"][1].startswith("text ")
    # a: .7*1 + .3*0 = .7 ; b: .7*0 + .3*1 = .3 -> first stage still wins at 0.3
    assert [x.chunk_id for x in out] == ["a", "b"]
    s = _settings().model_copy(update={"rerank_blend": 1.0})
    out = _rerank(s, "q", [P("a", "d", 0.5), P("b", "d", 0.4)], _http(h))
    assert [x.chunk_id for x in out] == ["b", "a"] and out[0].score > out[1].score


def test_rerank_depth_override():
    seen = {}

    def h(req):
        seen["n"] = len(__import__("json").loads(req.content)["documents"])
        return httpx.Response(
            200, json={"results": [{"index": i, "relevance_score": 1.0 - i / 100} for i in range(seen["n"])]}
        )

    ps = [P(f"c{i}", "d", 1 - i / 100) for i in range(80)]
    out = _rerank(_settings(), "q", ps, _http(h), 50)
    assert seen["n"] == 50 and len(out) == 80
