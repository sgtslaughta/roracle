import uuid
from types import SimpleNamespace

import pytest
from api_seed import FakeES, seed

from roracle import search_legacy
from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.models import Collection, CollectionItem, DocCitation, Tag, Vote

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
        "total": 0, "total_capped": False, "page": 1, "next": None, "results": [], "upgrading": True,
        "facets": {k: [] for k in ("tags", "year", "file_type", "org_unit", "collection", "author", "label")},
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
    assert {k: r[k] for k in ("title", "authors", "year", "file_type", "version", "venue", "org_unit", "owner_id", "flag", "cited_by_count", "tags", "auto_tags")} == {
        "title": "Shape", "authors": ["Ann"], "year": 2020, "file_type": "pdf", "version": 1, "venue": "ICML",
        "org_unit": "/", "owner_id": str(ana), "flag": None, "cited_by_count": 0, "tags": [], "auto_tags": [],
    }  # fmt: skip
    assert isinstance(r["score"], float)


def test_hit_auto_tags_subset(client, as_ana, ana, db_session, fes):
    d, v = seed(db_session, ana)
    db_session.add_all([Tag(document_id=d.id, tag="mine"), Tag(document_id=d.id, tag="gnn", source="llm")])
    db_session.flush()
    fes.hits = [hit(d, v)]
    r = get(client, as_ana, q="alpha").json()["results"][0]
    assert r["tags"] == ["gnn", "mine"] and r["auto_tags"] == ["gnn"]


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
    assert t(tags=["ml"], file_type=["docx"]) == ["B"]
    assert client.get("/search", headers=as_ana, params={"bogus": "x"}).status_code == 422  # unknown key


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

    monkeypatch.setattr(search_legacy, "search_chunks", boom)
    assert get(client, as_ana, q="x").status_code == 422


def test_unauthenticated(client):
    assert client.get("/search").status_code == 401


def test_facets_are_disjunctive(client, as_ana, ana, db_session, fes):
    a, _ = seed(db_session, ana, title="A")
    b, _ = seed(db_session, ana, title="B", filename="b.docx")
    c, _ = seed(db_session, ana, title="C", filename="c.docx")
    db_session.add_all(
        [Tag(document_id=a.id, tag="ml"), Tag(document_id=b.id, tag="bio"), Tag(document_id=c.id, tag="ml")]
    )
    db_session.flush()
    r = get(client, as_ana, q="", file_type="pdf").json()
    assert [x["title"] for x in r["results"]] == ["A"]
    assert r["facets"]["file_type"] == [{"value": "docx", "count": 2}, {"value": "pdf", "count": 1}]
    assert r["facets"]["tags"] == [{"value": "ml", "count": 1}]  # other facets reflect the file_type filter
    r = get(client, as_ana, q="", file_type="pdf", tags="bio").json()
    assert r["total"] == 0 and r["facets"]["file_type"] == [{"value": "docx", "count": 1}]


def test_disjunctive_facets_query_path(client, as_ana, ana, bob, db_session, fes):
    a, va = seed(db_session, ana, title="A")
    b, vb = seed(db_session, ana, title="B", filename="b.docx")
    h, vh = seed(db_session, bob, title="Hidden", visibility="private", filename="h.docx")
    fes.hits = [hit(a, va), hit(b, vb), hit(h, vh)]
    r = get(client, as_ana, q="alpha", file_type="pdf").json()
    assert r["facets"]["file_type"] == [{"value": "docx", "count": 1}, {"value": "pdf", "count": 1}]
    assert len(fes.calls) == 6  # filtered + unfiltered search (bm25 + knn + summary knn each)


def test_filtered_query_embeds_once(client, as_ana, ana, db_session, fes, monkeypatch):
    d, v = seed(db_session, ana, year=2021)
    fes.hits = [hit(d, v)]
    n = []
    from roracle.retrieval_util import embed as real

    def counting(*a, **k):
        n.append(1)
        return real(*a, **k)

    monkeypatch.setattr("roracle.retrieval_util.embed", counting)
    assert get(client, as_ana, q="alpha", file_type=["pdf"]).json()["total"] == 1
    assert len(n) == 1


def test_library_query_applies_similarity_floor(client, as_ana, ana, db_session, fes, app, settings):
    d, v = seed(db_session, ana)
    fes.hits = [hit(d, v)]
    assert get(client, as_ana, q="alpha").status_code == 200
    knn = [c["knn"] for c in fes.calls if "knn" in c]
    assert knn and all(k["similarity"] == settings.search_min_similarity == 0.35 for k in knn)
    app.state.settings = settings.model_copy(update={"search_min_similarity": 0})
    fes.calls.clear()
    get(client, as_ana, q="alpha")
    assert all("similarity" not in c["knn"] for c in fes.calls if "knn" in c)


# --- query path on a search-fields index (Task 8) ---


class ReadyES(FakeES):
    """FakeES on an index with the search-fields marker: non-empty `q` runs the new query path."""

    def __init__(self, hits=()):
        super().__init__(hits)
        meta = {"mappings": {"_meta": {"search_fields": 1}}}
        self.indices = SimpleNamespace(get_mapping=lambda index: {index: meta})


@pytest.fixture
def ready(app):
    f = ReadyES()
    app.dependency_overrides[get_es] = lambda: f
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    return f


def first_stage(es) -> list[dict]:
    return [c for c in es.calls if c.get("size") == 100 and "query" in c]  # BM25 bodies


def test_head_scope_sends_no_knn_and_excludes_on_title(client, as_ana, ana, db_session, ready):
    d, v = seed(db_session, ana, title="x")
    ready.hits = [hit(d, v)]
    r = get(client, as_ana, q="x -y", **{"in": "title"})
    assert r.status_code == 200 and r.json()["total"] == 1
    assert not any("knn" in c for c in ready.calls)
    (bm25,) = first_stage(ready)
    q = bm25["query"]["bool"]
    assert {"multi_match": {"query": "y", "type": "phrase", "fields": ["title"]}} in q["must_not"]
    assert {"term": {"doc_head": True}} in q["filter"]
    assert q["must"] == [{"multi_match": {"query": "x", "fields": ["title"]}}]


def test_new_params_reach_first_stage(client, as_ana, ana, db_session, ready):
    d, v = seed(db_session, ana)
    ready.hits = [hit(d, v)]
    r = get(client, as_ana, q="alpha", tags_not="a", sort="pages", has_doi="yes")
    assert r.status_code == 200, r.text  # Task 7's "browse only" 422 is gone
    bm25 = first_stage(ready)[0]["query"]["bool"]
    assert {"terms": {"tags": ["a"]}} in bm25["must_not"] and {"term": {"has_doi": True}} in bm25["filter"]
    knns = [c["knn"]["filter"] for c in ready.calls if "knn" in c]
    main = [k for k in knns if isinstance(k, dict)]  # {"bool": {filter, must_not}} when must_not is set
    assert len(main) == 2 and all({"terms": {"tags": ["a"]}} in k["bool"]["must_not"] for k in main)
    assert len(knns) == 4 and all("tags" not in str(k) for k in knns if k not in main)  # light universe pass


def test_cache_key_differs_by_include_exclude(client, as_ana, ana, db_session, ready):
    d, v = seed(db_session, ana)
    ready.hits = [hit(d, v)]
    n = lambda: len(first_stage(ready))
    get(client, as_ana, q="alpha", tags="a")
    once = n()
    get(client, as_ana, q="alpha", tags="a")
    assert n() == once  # cached: paging/re-sorting makes no ES call
    get(client, as_ana, q="alpha", tags_not="a")
    assert n() > once


def test_no_positive_word_is_422(client, as_ana, ready):
    r = get(client, as_ana, q="-a")
    assert r.status_code == 422 and r.json()["detail"] == "add at least one word to search for"
    assert ready.calls == []


def test_query_facets_acl_first_over_rechecked_ids(client, as_ana, ana, bob, db_session, ready):
    a, va = seed(db_session, ana, title="A")
    h, vh = seed(db_session, bob, title="Hidden", visibility="private")
    ready.hits = [hit(h, vh), hit(a, va)]
    body = get(client, as_ana, q="alpha", tags_not="zz").json()
    assert [x["title"] for x in body["results"]] == ["A"] and body["total"] == 1
    (fac,) = [c for c in ready.calls if c.get("size") == 0]
    flt = fac["query"]["bool"]["filter"]
    assert "visibility" in str(flt[0]) and {"terms": {"doc_id": [str(a.id)]}} in flt
    assert {"term": {"doc_head": True}} in flt and "aggs" in fac


def test_invisible_scope_is_absent_on_query_path(client, as_ana, bob, db_session, ready):
    _, bv = seed(db_session, bob, visibility="private")
    hidden = get(client, as_ana, q="alpha", cites=str(bv.document_id)).json()
    assert hidden == get(client, as_ana, q="alpha", cites=str(uuid.uuid4())).json()
    assert hidden["total"] == 0 and ready.calls == []


def _citer(db, target, owner, visibility):
    _, cv = seed(db, owner, title="citer", visibility=visibility)
    db.add(DocCitation(src_version_id=cv.id, ref_no=1, target_document_id=target.id, confidence=1.0))
    db.flush()


def test_invisible_citers_change_nothing(client, as_ana, ana, bob, db_session, ready):
    a, va = seed(db_session, ana, title="A")
    b, vb = seed(db_session, ana, title="B")
    _citer(db_session, a, ana, "everyone")
    ready.hits = [hit(b, vb), hit(a, va)]
    reqs = [{"sort": "cited"}, {"cited_min": 1}, {"cited_min": 2}, {"cited_min": 1, "sort": "title"}]
    before = [get(client, as_ana, q="alpha", **r).json() for r in reqs]
    for _ in range(3):
        _citer(db_session, b, bob, "private")
        _citer(db_session, a, bob, "private")
    after = [get(client, as_ana, q="alpha", **r).json() for r in reqs]
    assert before == after
    assert [x["title"] for x in before[0]["results"]] == ["A", "B"]
    assert before[1]["total"] == 1 and before[2]["total"] == 0
    assert [x["cited_by_count"] for x in before[0]["results"]] == [1, 0]
