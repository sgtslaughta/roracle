import uuid
from types import SimpleNamespace

import pytest
from api_seed import seed
from elasticsearch import NotFoundError

from roracle.auth import Principal
from roracle.chat import retrieve as rt
from roracle.chat.retrieve import Selection, Tab, Viewer, retrieve_for_chat
from roracle.models import Collection, CollectionItem, User


class FakeES:
    """Ignores the ACL (PG is the authority) but honours the doc_id terms filter."""

    def __init__(self, hits=(), cap=None):
        self.hits, self.calls, self.cap = list(hits), [], cap

    def search(self, **kw):
        self.calls.append(kw)
        flt = kw.get("knn", {}).get("filter") or kw["query"]["bool"]["filter"]
        ids = next((f["terms"]["doc_id"] for f in flt if "doc_id" in f.get("terms", {})), None)
        hits = [h for h in self.hits if ids is None or h["_source"]["doc_id"] in ids]
        if ids is None and self.cap:
            hits = hits[: self.cap]
        return {"hits": {"hits": hits}}


class FakeLLM:
    embeddings = SimpleNamespace(
        create=lambda model, input: SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[0.1] * 768) for i in range(len(input))]
        )
    )


def hit(doc, ver, chunk=0, pages=(1,), text="alpha text"):
    return {
        "_source": {
            "chunk_id": f"{ver.id}:{chunk}", "doc_id": str(doc.id), "version_id": str(ver.id), "text": text,
            "pages": list(pages), "bboxes": [], "title": doc.title, "authors": ["Ann"], "year": 2020,
        },
    }  # fmt: skip


@pytest.fixture
def me(db_session, user):
    return Principal(user.id, "sub-1", "researcher", (), False)


@pytest.fixture
def other(db_session):
    u = User(id=uuid.uuid4(), sub="sub-2", username="other", email="o@example.org")
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture
def calls(monkeypatch):
    seen = []
    real = rt.search_chunks

    def spy(*a, **kw):
        seen.append((a, kw))
        return real(*a, **kw)

    monkeypatch.setattr(rt, "search_chunks", spy)
    return seen


def run(db, es, settings, me, viewer=None, scope="all", pins=(), excludes=(), k=None, q="alpha"):
    return retrieve_for_chat(
        db, es, FakeLLM(), settings, me, q, viewer or Viewer(), scope, list(pins), list(excludes), k
    )


def ids(ps):
    return [x.doc_id for x in ps]


def test_invisible_doc_everywhere_contributes_nothing(db_session, settings, me, other, calls):
    mine, mv = seed(db_session, me.user_id, title="Mine")
    secret, sv = seed(db_session, other.id, title="Secret", visibility="private")
    es = FakeES([hit(secret, sv), hit(mine, mv)])
    v = Viewer(
        tabs=[Tab(doc_id=str(secret.id))], selection=Selection(doc_id=str(secret.id), page=1, text="leak")
    )
    out = run(db_session, es, settings, me, v, pins=[str(secret.id)])
    assert ids(out) == [str(mine.id)]
    assert all(x.chunk_id != "selection" for x in out)
    out = run(db_session, es, settings, me, v, scope="open", pins=[str(secret.id)])
    assert out == []


def test_stale_or_unsearchable_version_dropped(db_session, settings, me, other):
    d, v = seed(db_session, other.id, versions=2)  # v is latest; hit points at v1
    old = d.id
    from roracle.models import DocumentVersion

    v1 = db_session.query(DocumentVersion).filter_by(document_id=old, version=1).one()
    pend, pv = seed(db_session, other.id, stage="embedded")
    es = FakeES([hit(d, v1), hit(pend, pv), hit(d, v, chunk=1)])
    out = run(db_session, es, settings, me)
    assert [x.chunk_id for x in out] == [f"{v.id}:1"]


def test_scope_open_restricts_to_tabs(db_session, settings, me, other):
    a, av = seed(db_session, other.id, title="A")
    b, bv = seed(db_session, other.id, title="B")
    es = FakeES([hit(a, av), hit(b, bv)])
    out = run(db_session, es, settings, me, Viewer(tabs=[Tab(doc_id=str(b.id))]), scope="open")
    assert ids(out) == [str(b.id)]
    for c in es.calls:
        flt = c.get("knn", {}).get("filter") or c["query"]["bool"]["filter"]
        assert {"terms": {"doc_id": [str(b.id)]}} in flt


def test_scope_open_no_tabs_is_empty_or_selection_only(db_session, settings, me, other):
    a, av = seed(db_session, other.id)
    es = FakeES([hit(a, av)])
    assert run(db_session, es, settings, me, scope="open") == []
    assert es.calls == []
    sel = Selection(doc_id=str(a.id), page=3, text="picked")
    out = run(db_session, es, settings, me, Viewer(selection=sel), scope="open")
    assert [x.chunk_id for x in out] == ["selection"]


def test_collection_scope(db_session, settings, me, other, calls):
    a, av = seed(db_session, other.id, title="A")
    b, bv = seed(db_session, other.id, title="B")
    mine = Collection(owner_id=me.user_id, name="m")
    foreign = Collection(owner_id=other.id, name="f")
    db_session.add_all([mine, foreign])
    db_session.flush()
    db_session.add_all(
        [CollectionItem(collection_id=mine.id, document_id=a.id),
         CollectionItem(collection_id=foreign.id, document_id=b.id)]
    )  # fmt: skip
    db_session.flush()
    es = FakeES([hit(a, av), hit(b, bv)])
    assert ids(run(db_session, es, settings, me, scope=f"collection:{mine.id}")) == [str(a.id)]
    n = len(es.calls)
    assert run(db_session, es, settings, me, scope=f"collection:{foreign.id}") == []
    assert run(db_session, es, settings, me, scope="collection:nope") == []
    assert len(es.calls) == n  # no ES call for foreign/unknown


def test_bad_scope(db_session, settings, me):
    with pytest.raises(ValueError):
        run(db_session, FakeES(), settings, me, scope="bogus")


def test_exclude_and_ignored(db_session, settings, me, other):
    a, av = seed(db_session, other.id)
    b, bv = seed(db_session, other.id)
    c, cv = seed(db_session, other.id)
    es = FakeES([hit(a, av), hit(b, bv), hit(c, cv)])
    v = Viewer(ignored=[str(b.id)], selection=Selection(doc_id=str(b.id), page=1, text="x"))
    out = run(db_session, es, settings, me, v, excludes=[str(a.id)])
    assert ids(out) == [str(c.id)]


def test_page_and_tab_boost(db_session, settings, me, other):
    a, av = seed(db_session, other.id, title="A")
    b, bv = seed(db_session, other.id, title="B")
    es = FakeES([hit(a, av, pages=(1,)), hit(b, bv, pages=(5,))])
    assert ids(run(db_session, es, settings, me)) == [str(a.id), str(b.id)]
    top = 2 / 62  # B's main-search score (rank 2); the tab search must not inflate it
    out = run(db_session, es, settings, me, Viewer(tabs=[Tab(doc_id=str(b.id), page=2)]))
    assert ids(out) == [str(b.id), str(a.id)]
    assert next(x for x in out if x.doc_id == str(b.id)).score == pytest.approx(top * 1.5)
    out = run(db_session, es, settings, me, Viewer(tabs=[Tab(doc_id=str(b.id), page=5)]))
    assert next(x for x in out if x.doc_id == str(b.id)).score == pytest.approx(top * 2.0)


def test_tab_second_search_adds_candidates(db_session, settings, me, other, calls):
    docs = [seed(db_session, other.id, title=f"D{i}") for i in range(3)]
    es = FakeES([hit(d, v) for d, v in docs])
    run(db_session, es, settings, me, Viewer(tabs=[Tab(doc_id=str(docs[2][0].id))]))
    assert [kw["size"] for _, kw in calls] == [50, 20]
    assert calls[1][1]["doc_ids"] == [str(docs[2][0].id)]


def test_pin_forces_inclusion(db_session, settings, me, other):
    docs = [seed(db_session, other.id, title=f"D{i}") for i in range(8)]
    es = FakeES([hit(d, v) for d, v in docs])
    last = str(docs[7][0].id)
    assert last not in ids(run(db_session, es, settings, me, k=4))
    out = run(db_session, es, settings, me, pins=[last], k=4)
    assert len(out) == 4 and last in ids(out)
    assert ids(out)[:3] == [str(d.id) for d, _ in docs[:3]]
    assert run(db_session, es, settings, me, pins=[str(uuid.uuid4())], k=4) != []


def test_truncation_and_k_clamp(db_session, settings, me, other):
    docs = [seed(db_session, other.id) for _ in range(14)]
    es = FakeES([hit(d, v, text="x" * 5000) for d, v in docs])
    out = run(db_session, es, settings, me)
    assert len(out) == 8 and all(len(x.text) == 1200 for x in out)
    assert len(run(db_session, es, settings, me, k=1)) == 4
    assert len(run(db_session, es, settings, me, k=99)) == 12


def test_selection_prepended_not_counted(db_session, settings, me, other):
    docs = [seed(db_session, other.id, authors=("Zed",), year=1999) for _ in range(6)]
    es = FakeES([hit(d, v) for d, v in docs])
    d0, v0 = docs[0]
    sel = Selection(doc_id=str(d0.id), page=7, text="s" * 2000)
    out = run(db_session, es, settings, me, Viewer(selection=sel), k=4)
    assert len(out) == 5
    s = out[0]
    assert (s.chunk_id, s.title, s.pages, s.version_id) == ("selection", "Selected passage", [7], str(v0.id))
    assert len(s.text) == 1200 and s.authors == ["Zed"] and s.year == 1999


def test_keyword_only_call_and_min_similarity(db_session, settings, me, other, calls):
    a, av = seed(db_session, other.id)
    es = FakeES([hit(a, av)])
    s = settings.model_copy(update={"chat_min_similarity": 0.4})
    run(db_session, es, s, me)
    args, kw = calls[0]
    assert len(args) == 4
    assert kw["query"] == "alpha" and kw["filters"] == {} and kw["doc_ids"] is None and kw["size"] == 50
    assert kw["min_similarity"] == 0.4
    assert next(c for c in es.calls if "knn" in c)["knn"]["similarity"] == 0.4
    s0 = settings.model_copy(update={"chat_min_similarity": 0})
    es2 = FakeES([hit(a, av)])
    run(db_session, es2, s0, me)
    assert "similarity" not in next(c for c in es2.calls if "knn" in c)["knn"]


def test_missing_index_is_empty(db_session, settings, me):
    class Boom:
        def search(self, **kw):
            raise NotFoundError("nf", SimpleNamespace(status=404), {})

    assert run(db_session, Boom(), settings, me) == []


def test_tab_doc_missing_from_corpus_does_not_crowd_out(db_session, settings, me, other):
    docs = [seed(db_session, other.id, title=f"D{i}") for i in range(6)]
    es = FakeES([hit(d, v) for d, v in docs], cap=3)  # corpus search sees only D0-D2
    tab = str(docs[5][0].id)
    out = run(db_session, es, settings, me, Viewer(tabs=[Tab(doc_id=tab)]), k=4)
    assert ids(out)[0] == tab
    assert ids(out)[1:] == [str(d.id) for d, _ in docs[:3]]
    assert out[0].score == pytest.approx(2 / 63 * 2.0)  # on the tab's page


def test_soft_deleted_doc_dropped(db_session, settings, me, other):
    from datetime import UTC, datetime

    a, av = seed(db_session, other.id)
    a.deleted_at = datetime.now(UTC)
    db_session.flush()
    sel = Selection(doc_id=str(a.id), page=1, text="x")
    assert run(db_session, FakeES([hit(a, av)]), settings, me, Viewer(selection=sel)) == []


def test_exclude_beats_pin_and_selection(db_session, settings, me, other):
    a, av = seed(db_session, other.id)
    b, bv = seed(db_session, other.id)
    es = FakeES([hit(a, av), hit(b, bv)])
    sel = Selection(doc_id=str(a.id), page=1, text="x")
    out = run(db_session, es, settings, me, Viewer(selection=sel), pins=[str(a.id)], excludes=[str(a.id)])
    assert ids(out) == [str(b.id)]


def test_invalid_ids_ignored(db_session, settings, me, other):
    a, av = seed(db_session, other.id)
    v = Viewer(tabs=[Tab(doc_id="junk")], ignored=["x"], selection=Selection(doc_id="junk", page=1, text="t"))
    out = run(db_session, FakeES([hit(a, av)]), settings, me, v, pins=["bad"], excludes=["worse"])
    assert ids(out) == [str(a.id)]
    with pytest.raises(ValueError):
        Tab(doc_id="a", page=0)
