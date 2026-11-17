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
