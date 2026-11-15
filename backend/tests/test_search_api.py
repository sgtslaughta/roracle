from types import SimpleNamespace

import pytest
from api_seed import FakeES, seed

from roracle.api.routes import search as search_mod
from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.models import Collection, CollectionItem, Tag, Vote

pytestmark = pytest.mark.usefixtures("_blob_store_override")


def hit(doc, ver, text="alpha text", chunk=0):
    return {
        "_source": {
            "chunk_id": f"{ver.id}:{chunk}", "doc_id": str(doc.id), "version_id": str(ver.id), "text": text,
            "pages": [2], "bboxes": [{"page": 2, "l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "BOTTOMLEFT"}],
            "title": doc.title, "authors": ["Ann"], "year": 2020,
        },
        "highlight": {"text": ["<em>alpha</em> text"]},
    }  # fmt: skip


class FakeLLM:
    embeddings = SimpleNamespace(
        create=lambda model, input: SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[0.1] * 768) for i in range(len(input))]
        )
    )


@pytest.fixture
def fes(app):
    f = FakeES()
    app.dependency_overrides[get_es] = lambda: f
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    return f


def get(client, headers, **params):
    return client.get("/search", headers=headers, params=params)


def test_invisible_doc_contributes_nothing(client, as_ana, ana, bob, db_session, fes):
    d1, v1 = seed(db_session, ana, title="Mine", year=2021, authors=("Ann",))
    d2, v2 = seed(db_session, bob, title="Secret", visibility="private", year=1999, authors=("Zed",))
    db_session.add_all([Tag(document_id=d1.id, tag="ml"), Tag(document_id=d2.id, tag="secret")])
    db_session.flush()
    fes.hits = [hit(d2, v2), hit(d1, v1)]  # fake ES ignores ACL: PG visibility is the second line
    body = get(client, as_ana, q="alpha").json()
    assert body["total"] == 1 and [r["doc_id"] for r in body["results"]] == [str(d1.id)]
    f = body["facets"]
    assert f["tags"] == [{"value": "ml", "count": 1}] and f["year"] == [{"value": "2021", "count": 1}]
    assert f["author"] == [{"value": "Ann", "count": 1}]
    assert f["file_type"] == [{"value": "pdf", "count": 1}] and f["org_unit"] == [{"value": "/", "count": 1}]


def test_all_invisible_is_empty_everywhere(client, as_ana, bob, db_session, fes):
    d, v = seed(db_session, bob, visibility="private")
    fes.hits = [hit(d, v)]
    body = get(client, as_ana, q="alpha").json()
    assert body == {
        "total": 0, "page": 1, "results": [],
        "facets": {k: [] for k in ("tags", "year", "file_type", "org_unit", "collection", "author")},
    }  # fmt: skip


def test_hit_shape(client, as_ana, ana, bob, db_session, fes):
    d, v = seed(db_session, ana, title="Shape", venue="ICML")
    db_session.add_all(
        [Vote(user_id=ana, document_id=d.id, value=1), Vote(user_id=bob, document_id=d.id, value=-1)]
    )
    db_session.flush()
    fes.hits = [hit(d, v)]
    r = get(client, as_ana, q="alpha").json()["results"][0]
    assert r["votes"] == {"up": 1, "down": 1, "mine": 1}
    assert r["passages"] == [
        {"page": 2, "chunk_id": f"{v.id}:0", "text": "alpha text", "highlight": "<em>alpha</em> text",
         "bbox": {"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "BOTTOMLEFT"}}
    ]  # fmt: skip
    assert {k: r[k] for k in ("title", "authors", "year", "file_type", "version", "venue", "org_unit", "owner_id", "flag", "cited_by_count", "tags")} == {
        "title": "Shape", "authors": ["Ann"], "year": 2020, "file_type": "pdf", "version": 1, "venue": "ICML",
        "org_unit": "/", "owner_id": str(ana), "flag": None, "cited_by_count": 0, "tags": [],
    }  # fmt: skip
    assert isinstance(r["score"], float)


def test_sorts(client, as_ana, ana, bob, db_session, fes):
    a, va = seed(db_session, ana, title="banana", hours=1)
    b, vb = seed(db_session, ana, title="Apple", hours=3)
    c, vc = seed(db_session, ana, title="cherry", hours=2)
    db_session.add_all([Vote(user_id=ana, document_id=c.id, value=1), Vote(user_id=bob, document_id=c.id, value=1),
                        Vote(user_id=ana, document_id=a.id, value=-1)])  # fmt: skip
    db_session.flush()
    fes.hits = [hit(a, va), hit(b, vb), hit(c, vc)]  # relevance order a, b, c (votes then reorder slightly)
    titles = lambda sort: [r["title"] for r in get(client, as_ana, q="x", sort=sort).json()["results"]]
    assert titles("newest") == ["Apple", "cherry", "banana"]
    assert titles("title") == ["Apple", "banana", "cherry"]
    assert titles("votes") == ["cherry", "Apple", "banana"]
    assert titles("relevance") == ["cherry", "Apple", "banana"]  # rank order a,b,c adjusted by votes


def test_paging(client, as_ana, ana, db_session, fes):
    for i in range(5):
        seed(db_session, ana, title=f"t{i}", hours=i)
    r = get(client, as_ana, q="", sort="newest", size=2, page=2).json()
    assert r["total"] == 5 and r["page"] == 2 and [x["title"] for x in r["results"]] == ["t2", "t1"]
    assert get(client, as_ana, q="", size=2, page=9).json()["results"] == []


def test_empty_q_browses_without_es(client, as_ana, ana, bob, db_session, fes):
    seed(db_session, ana, title="old", hours=1)
    seed(db_session, bob, title="new", hours=5)
    seed(db_session, bob, title="hidden", visibility="private", hours=9)
    seed(db_session, ana, title="parsing", stage="parsed", hours=8)
    for q in ("", "   "):
        r = get(client, as_ana, q=q).json()
        assert [x["title"] for x in r["results"]] == ["new", "old"] and r["total"] == 2
        assert r["results"][0]["passages"] == []
    assert get(client, as_ana).json()["total"] == 2  # q omitted
    assert fes.calls == []


def test_filters_or_within_and_across(client, as_ana, ana, db_session, fes):
    a, _ = seed(db_session, ana, title="A", year=2020)
    b, _ = seed(db_session, ana, title="B", year=2021, filename="b.docx")
    c, _ = seed(db_session, ana, title="C", year=2021)
    db_session.add_all(
        [Tag(document_id=a.id, tag="ml"), Tag(document_id=b.id, tag="ml"), Tag(document_id=c.id, tag="bio")]
    )
    db_session.flush()
    t = lambda **p: sorted(
        r["title"] for r in client.get("/search", headers=as_ana, params=p).json()["results"]
    )
    assert t(tags=["ml", "bio"]) == ["A", "B", "C"]
    assert t(tags=["ml"], year=[2021]) == ["B"]
    assert t(file_type="docx", bogus="x") == ["B"]  # unknown params ignored


def test_collection_facet_and_filter(client, as_ana, as_bob, ana, bob, db_session, fes):
    a, va = seed(db_session, ana, title="In")
    seed(db_session, ana, title="Out")
    mine, theirs = Collection(owner_id=ana, name="Reading"), Collection(owner_id=bob, name="Bobs")
    db_session.add_all([mine, theirs])
    db_session.flush()
    db_session.add_all(
        [
            CollectionItem(collection_id=mine.id, document_id=a.id),
            CollectionItem(collection_id=theirs.id, document_id=a.id),
        ]
    )
    db_session.flush()
    r = get(client, as_ana, q="", collection=str(mine.id)).json()
    assert [x["title"] for x in r["results"]] == ["In"]
    assert r["facets"]["collection"] == [
        {"value": str(mine.id), "label": "Reading", "count": 1}
    ]  # never bob's
    assert get(client, as_ana, q="", collection=str(theirs.id)).json()["total"] == 0  # someone else's
    assert get(client, as_ana, q="", collection="nope").json()["total"] == 0
    fes.hits = [hit(a, va)]
    assert get(client, as_ana, q="alpha", collection=str(mine.id)).json()["total"] == 1
    assert {"terms": {"doc_id": [str(a.id)]}} in fes.calls[0]["query"]["bool"]["filter"]  # R10 pre-filter
    n = len(fes.calls)
    assert get(client, as_ana, q="alpha", collection=str(theirs.id)).json()["total"] == 0
    assert len(fes.calls) == n  # no ES call, nothing to search


@pytest.mark.parametrize("params", [{"size": 51}, {"size": 0}, {"page": 0}, {"sort": "bogus"}, {"year": "x"}])
def test_bad_params_422(client, as_ana, fes, params):
    assert get(client, as_ana, q="x", **params).status_code == 422


def test_max_size_ok(client, as_ana, fes):
    assert get(client, as_ana, q="x", size=50).status_code == 200


def test_search_value_error_is_422(client, as_ana, fes, monkeypatch):
    def boom(*a, **k):
        raise ValueError("unknown filter: x")

    monkeypatch.setattr(search_mod, "search_chunks", boom)
    assert get(client, as_ana, q="x").status_code == 422


def test_unauthenticated(client):
    assert client.get("/search").status_code == 401


def test_facets_are_disjunctive(client, as_ana, ana, db_session, fes):
    a, _ = seed(db_session, ana, title="A", year=2021)
    b, _ = seed(db_session, ana, title="B", year=2022)
    c, _ = seed(db_session, ana, title="C", year=2022)
    db_session.add_all(
        [Tag(document_id=a.id, tag="ml"), Tag(document_id=b.id, tag="bio"), Tag(document_id=c.id, tag="ml")]
    )
    db_session.flush()
    r = get(client, as_ana, q="", year=2021).json()
    assert [x["title"] for x in r["results"]] == ["A"]
    assert r["facets"]["year"] == [{"value": "2022", "count": 2}, {"value": "2021", "count": 1}]
    assert r["facets"]["tags"] == [{"value": "ml", "count": 1}]  # other facets reflect the year filter
    r = get(client, as_ana, q="", year=2021, tags="bio").json()
    assert r["total"] == 0 and r["facets"]["year"] == [{"value": "2022", "count": 1}]


def test_disjunctive_facets_query_path(client, as_ana, ana, bob, db_session, fes):
    a, va = seed(db_session, ana, title="A", year=2021)
    b, vb = seed(db_session, ana, title="B", year=2022)
    h, vh = seed(db_session, bob, title="Hidden", visibility="private", year=2022)
    fes.hits = [hit(a, va), hit(b, vb), hit(h, vh)]
    r = get(client, as_ana, q="alpha", year=2021).json()
    assert r["facets"]["year"] == [{"value": "2021", "count": 1}, {"value": "2022", "count": 1}]
    assert len(fes.calls) == 4  # filtered + unfiltered search (bm25 + knn each)


def test_filtered_query_embeds_once(client, as_ana, ana, db_session, fes, monkeypatch):
    d, v = seed(db_session, ana, year=2021)
    fes.hits = [hit(d, v)]
    n = []
    from roracle.retrieval import embed as real

    def counting(*a, **k):
        n.append(1)
        return real(*a, **k)

    monkeypatch.setattr("roracle.retrieval.embed", counting)
    assert get(client, as_ana, q="alpha", year=[2021]).json()["total"] == 1
    assert len(n) == 1
