"""Task 10.12: `mode=quick` is the palette's title search: ACL-filtered BM25 only, no embed, no rerank."""

import pytest
from api_seed import FakeES, seed
from test_search_api import hit

from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm

pytestmark = pytest.mark.usefixtures("_blob_store_override")


class NoLLM:
    @property
    def embeddings(self):
        raise AssertionError("quick mode must not embed")


@pytest.fixture
def fes(app):
    f = FakeES()
    app.dependency_overrides[get_es] = lambda: f
    app.dependency_overrides[get_llm] = lambda: NoLLM()
    return f


def test_quick_is_one_acl_filtered_title_bm25_search_without_embed_or_rerank(
    client, as_ana, ana, bob, db_session, fes
):
    d1, v1 = seed(db_session, ana, title="Alpha paper")
    d2, v2 = seed(db_session, bob, title="Alpha secret", visibility="private")
    fes.hits = [
        hit(d2, v2),
        hit(d1, v1),
        hit(d1, v1, chunk=1),
    ]  # fake ES ignores ACL; PG re-check drops bob's
    r = client.get("/search", headers=as_ana, params={"q": "alpha", "mode": "quick", "size": 50})
    assert r.status_code == 200
    assert [h["doc_id"] for h in r.json()["results"]] == [str(d1.id)]  # deduped, invisible doc gone
    assert len(fes.calls) == 1
    body = fes.calls[0]
    assert body["size"] <= 40 and "knn" not in body and "rerank" not in str(body)
    filt = body["query"]["bool"]["filter"]
    assert any("is_latest" in str(f) for f in filt) and len(filt) >= 2  # ACL + latest
    assert body["query"]["bool"]["must"][0]["multi_match"]["fields"] == ["title"]


def test_quick_returns_at_most_10_documents(client, as_ana, ana, db_session, fes):
    hits = []
    for i in range(14):
        d, v = seed(db_session, ana, title=f"Alpha {i}")
        hits.append(hit(d, v))
    fes.hits = hits
    r = client.get("/search", headers=as_ana, params={"q": "alpha", "mode": "quick"})
    assert len(r.json()["results"]) == 10


def test_quick_rejects_filters_and_paging_and_shapes_the_label_like_search(
    client, as_ana, ana, db_session, fes
):
    d, v = seed(db_session, ana, title="Alpha paper")
    fes.hits = [hit(d, v)]
    for extra in ({"tags": "x"}, {"year": 2020}, {"page": 2}, {"collection": "c"}, {"after": "x"}):
        assert (
            client.get("/search", headers=as_ana, params={"q": "alpha", "mode": "quick", **extra}).status_code
            == 422
        )
    r = client.get("/search", headers=as_ana, params={"q": "alpha", "mode": "quick"})
    assert isinstance(r.json()["results"][0]["label"], dict)  # label JSON, not a bare id


def test_a_throttled_embedding_endpoint_is_a_fast_503_for_search(client, as_ana, ana, db_session, app, fes):
    import time
    from types import SimpleNamespace

    class Throttled(Exception):
        status_code = 429
        response = SimpleNamespace(headers={"retry-after": "30"})

    def boom(model, input):
        raise Throttled("slow down")

    app.dependency_overrides[get_llm] = lambda: SimpleNamespace(embeddings=SimpleNamespace(create=boom))
    seed(db_session, ana, title="x")
    t = time.monotonic()
    r = client.get("/search", headers=as_ana, params={"q": "uncached throttled query"})
    assert r.status_code == 503 and time.monotonic() - t < 1.5
