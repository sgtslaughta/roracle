"""Task 10.8: one msearch round trip, light facet pass, memoized embeds, page cache that never widens access."""

from types import SimpleNamespace

import pytest
from api_seed import FakeES, seed
from test_search_api import FakeLLM, get, hit

from roracle import retrieval
from roracle.api.routes import search as search_mod
from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.auth import Principal
from roracle.config import Settings
from roracle.labels.policy import Policy
from roracle.retrieval import LIGHT_SOURCE, rrf, search_chunks

pytestmark = pytest.mark.usefixtures("_blob_store_override")


class RecordingEs:
    """Real msearch contract: one request carrying N bodies. No `search` method: a second round trip would fail."""

    def __init__(self, bm25=(), knn=(), sknn=(), sknn_error=None):
        self.requests, self.lists, self.sknn_error = [], [list(bm25), list(knn), list(sknn)], sknn_error

    def msearch(self, index=None, searches=()):
        self.requests.append((index, list(searches)))
        bodies = list(searches)[1::2]
        out = []
        for i, body in enumerate(bodies):
            if i == 2 and self.sknn_error:
                out.append({"error": {"reason": "no such field"}, "status": 400})
            else:
                out.append({"hits": {"hits": self.lists[i]}})
        return {"responses": out}


def h(chunk, text="t"):
    return {
        "_source": {"chunk_id": chunk, "doc_id": "d" + chunk, "version_id": "v", "text": text, "pages": [1]},
        "highlight": {"text": [f"<em>{chunk}</em>"]},
    }


def settings():
    return Settings(
        database_url="x", s3_endpoint="x", s3_access_key="x", s3_secret_key="x", oidc_issuer="x",
        oidc_jwks_url="x",
    )  # fmt: skip


ADMIN = Principal(user_id="u", sub="s", username="u", groups=(), is_admin=True)


def run(es, **kw):
    return search_chunks(
        es, None, settings(), ADMIN, "q", policy=Policy([]), filters={}, qvec=[0.1], **kw
    )  # fmt: skip


def test_one_msearch_with_three_bodies_and_rrf_unchanged():
    bm25, knn, sknn = [h("a"), h("b"), h("c")], [h("b"), h("d"), h("a")], [h("e"), h("b")]
    es = RecordingEs(bm25, knn, sknn)
    out = run(es)
    assert len(es.requests) == 1
    bodies = es.requests[0][1][1::2]
    assert len(bodies) == 3
    assert "query" in bodies[0] and "highlight" in bodies[0]
    assert [b["knn"]["field"] for b in bodies[1:]] == ["embedding", "summary_embedding"]
    expect = rrf([[x["_source"]["chunk_id"] for x in lst] for lst in (bm25, knn, sknn)])
    assert [x.chunk_id for x in out] == sorted(expect, key=lambda c: -expect[c])
    assert (
        next(x for x in out if x.chunk_id == "a").highlight == "<em>a</em>"
    )  # BM25 hit carries the highlight


def test_summary_knn_off_sends_two_bodies_and_missing_field_is_tolerated():
    es = RecordingEs([h("a")], [h("b")])
    run(es, summary_knn=False)
    assert len(es.requests[0][1][1::2]) == 2
    es = RecordingEs([h("a")], [h("b")], sknn_error=True)
    assert {x.chunk_id for x in run(es)} == {"a", "b"}  # sub-search 400 -> summary list skipped


def test_light_pass_has_no_highlight_text_or_rerank():
    es = RecordingEs([h("a", "")], [h("b", "")])
    s = settings().model_copy(update={"rerank_url": "http://rr"})
    out = search_chunks(
        es, None, s, ADMIN, "q", policy=Policy([]), filters={}, qvec=[0.1], light=True, http=None
    )  # a rerank call would try the network and fail the test
    bodies = es.requests[0][1][1::2]
    assert all(b["_source"] == LIGHT_SOURCE for b in bodies) and "highlight" not in bodies[0]
    assert {x.doc_id for x in out} == {"da", "db"}


def test_embed_query_is_memoized_per_model_and_prompt():
    calls = []
    llm = SimpleNamespace(
        embeddings=SimpleNamespace(
            create=lambda model, input: (
                calls.append(model) or SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.5] * 768)])
            )
        )
    )
    s = settings()
    a = retrieval.embed_query(llm, s, "hello")
    assert retrieval.embed_query(llm, s, "hello") == a and len(calls) == 1
    retrieval.embed_query(llm, s, "other")
    retrieval.embed_query(llm, s.model_copy(update={"embed_model": "m2"}), "hello")
    assert len(calls) == 3
    off = s.model_copy(update={"embed_cache_size": 0})
    retrieval.embed_query(llm, off, "hello")
    retrieval.embed_query(llm, off, "hello")
    assert len(calls) == 5


@pytest.fixture
def fes(app):
    f = FakeES()
    app.dependency_overrides[get_es] = lambda: f
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    return f


def test_page_two_within_ttl_makes_no_es_call(client, as_ana, ana, db_session, fes):
    docs = [seed(db_session, ana, title=f"d{i}", hours=i) for i in range(5)]
    fes.hits = [hit(d, v) for d, v in docs]
    first = get(client, as_ana, q="alpha", size=2).json()
    n = len(fes.calls)
    assert n == 3  # BM25 + kNN + summary kNN, one msearch
    p2 = get(client, as_ana, q="alpha", size=2, page=2).json()
    assert len(fes.calls) == n and p2["page"] == 2 and p2["results"] != first["results"]


def test_cache_is_never_shared_between_principals(client, as_ana, as_bob, ana, db_session, fes):
    d, v = seed(db_session, ana, title="x")
    fes.hits = [hit(d, v)]
    get(client, as_ana, q="alpha")
    n = len(fes.calls)
    get(client, as_bob, q="alpha")
    assert len(fes.calls) > n  # bob's request missed ana's entry
    m = len(fes.calls)
    get(client, as_ana, q="alpha", tags="t")
    assert len(fes.calls) > m  # different filters, different entry


def test_r19_drops_a_doc_made_private_during_the_ttl(client, as_ana, as_bob, ana, db_session, fes):
    d, v = seed(db_session, ana, title="open")
    fes.hits = [hit(d, v)]
    assert [r["title"] for r in get(client, as_bob, q="alpha").json()["results"]] == ["open"]
    n = len(fes.calls)
    d.visibility = "private"
    db_session.flush()
    assert get(client, as_bob, q="alpha").json()["results"] == []  # ES result came from cache, PG said no
    assert len(fes.calls) == n


def test_universe_pass_is_light_and_unreranked_and_the_client_is_shared(
    client, as_ana, ana, db_session, fes, monkeypatch
):
    d, v = seed(db_session, ana, title="x", year=2020)
    fes.hits = [hit(d, v)]
    seen = []
    real = search_mod.search_chunks

    def spy(*a, **kw):
        seen.append((kw.get("light", False), kw.get("rerank_depth"), kw.get("http")))
        return real(*a, **kw)

    monkeypatch.setattr(search_mod, "search_chunks", spy)
    get(client, as_ana, q="alpha", year=2020)
    assert [(s[0], s[1]) for s in seen] == [(False, 50), (True, 50)]
    get(client, as_ana, q="alpha2", year=2020)
    clients = {id(s[2]) for s in seen if s[2] is not None}
    assert len(clients) == 1 and client.app.state.http is seen[0][2]


def test_cache_key_changes_with_policy():
    from roracle.labels.policy import LabelInfo

    base = {
        "id": "1", "name": "n", "colour": "#000000", "description": "", "rank": 0, "block_agents": False,
        "block_export": False, "reason_on_lower": False, "is_default": True,
    }  # fmt: skip
    a = Policy([LabelInfo(**base, exclude_llm=False)])
    b = Policy([LabelInfo(**base, exclude_llm=True)])
    assert a.version != b.version and a.version == Policy([LabelInfo(**base, exclude_llm=False)]).version


def test_empty_results_are_not_cached_so_a_new_grant_shows_at_once(client, as_bob, ana, db_session, fes):
    d, v = seed(db_session, ana, title="later")
    assert get(client, as_bob, q="alpha").json()["results"] == []  # ES (which applies the ACL) found nothing
    n = len(fes.calls)
    fes.hits = [hit(d, v)]  # the doc has been shared with bob since
    assert [r["title"] for r in get(client, as_bob, q="alpha").json()["results"]] == ["later"]
    assert len(fes.calls) > n
