from uuid import uuid4

import pytest
from api_seed import seed
from test_citations import citing

from roracle.auth import Principal
from roracle.models import Collection, CollectionItem, DocCitation
from roracle.search_limits import BadInput
from roracle.search_params import SearchParams
from roracle.search_scope import SCOPE_IDS_MAX, resolve_scope


def _p(uid):
    return Principal(user_id=uid, sub="s", username="u", groups=(), is_admin=False)


def _coll(db, owner, docs, name="c"):
    c = Collection(owner_id=owner, name=name)
    db.add(c)
    db.flush()
    for d in docs:
        db.add(CollectionItem(collection_id=c.id, document_id=d.id))
    db.flush()
    return c


def test_foreign_collection_is_none(db_session, ana, bob):
    d, _ = seed(db_session, bob, title="x")
    c = _coll(db_session, bob, [d])
    assert resolve_scope(db_session, _p(ana), SearchParams(collection=[str(c.id)])) is None
    assert resolve_scope(db_session, _p(ana), SearchParams(collection=["junk"])) is None


def test_own_collection_includes_and_not_excludes(db_session, ana):
    d1, _ = seed(db_session, ana, title="a")
    d2, _ = seed(db_session, ana, title="b")
    c = _coll(db_session, ana, [d1, d2], "mine")
    s = resolve_scope(db_session, _p(ana), SearchParams(collection=[str(c.id)]))
    assert sorted(s.include) == sorted([str(d1.id), str(d2.id)]) and s.exclude == []
    assert s.colls[str(c.id)][0] == "mine"
    s = resolve_scope(db_session, _p(ana), SearchParams(collection_not=[str(c.id)]))
    assert s.include is None and sorted(s.exclude) == sorted([str(d1.id), str(d2.id)])


def test_cites_invisible_equals_random(db_session, ana, bob):
    hidden, _ = seed(db_session, bob, title="hidden", visibility="private")
    _, v = citing(db_session, bob, [{"title": "hidden"}])
    db_session.add(DocCitation(src_version_id=v.id, ref_no=1, target_document_id=hidden.id, confidence=1.0))
    db_session.flush()
    p = _p(ana)
    assert resolve_scope(db_session, p, SearchParams(cites=hidden.id)) is None
    assert resolve_scope(db_session, p, SearchParams(cites=uuid4())) is None


def test_cites_visible_only_visible_best_target_citers(db_session, ana, bob):
    x, _ = seed(db_session, ana, title="X")
    other, _ = seed(db_session, ana, title="Other")
    c1, v1 = citing(db_session, ana, [{"title": "X"}])
    c2, v2 = citing(db_session, ana, [{"title": "X"}])
    _, vh = citing(db_session, bob, [{"title": "X"}], visibility="private")  # ana cannot see it
    _, vl = citing(db_session, ana, [{"title": "X"}])  # best target is `other`
    for v, t, conf in ((v1, x, 1.0), (v2, x, 1.0), (vh, x, 1.0), (vl, x, 0.8), (vl, other, 0.9)):
        db_session.add(DocCitation(src_version_id=v.id, ref_no=1, target_document_id=t.id, confidence=conf))
    db_session.flush()
    s = resolve_scope(db_session, _p(ana), SearchParams(cites=x.id))
    assert sorted(s.include) == sorted([str(c1.id), str(c2.id)])
    c = _coll(db_session, ana, [c1])
    s = resolve_scope(db_session, _p(ana), SearchParams(cites=x.id, collection=[str(c.id)]))
    assert s.include == [str(c1.id)]


def test_scope_too_large(db_session, ana, monkeypatch):
    d, _ = seed(db_session, ana, title="a")
    c = _coll(db_session, ana, [d])
    monkeypatch.setattr("roracle.search_scope.SCOPE_IDS_MAX", 0)
    with pytest.raises(BadInput, match="too large"):
        resolve_scope(db_session, _p(ana), SearchParams(collection=[str(c.id)]))
    assert SCOPE_IDS_MAX == 10_000


def test_cites_visible_but_unsearchable_is_none(db_session, ana, bob):
    d, _ = seed(
        db_session, bob, title="ingesting", stage="embedded"
    )  # everyone-visible, no searchable version
    assert resolve_scope(db_session, _p(ana), SearchParams(cites=d.id)) is None


def test_collection_facet_ids_are_capped(db_session, ana, monkeypatch):
    """Facet counts load at most SCOPE_IDS_MAX collection items in all; a collection that does not fit gets no
    bucket (never an error), and the find box loads none."""
    a, b, c = (seed(db_session, ana, title=t)[0] for t in "abc")
    ca, cb, cc = (
        _coll(db_session, ana, [a], "a"),
        _coll(db_session, ana, [a, b], "b"),
        _coll(db_session, ana, [c], "c"),
    )
    monkeypatch.setattr("roracle.search_scope.SCOPE_IDS_MAX", 2)
    s = resolve_scope(db_session, _p(ana), SearchParams())
    assert set(s.colls) == {str(ca.id), str(cc.id)} and str(cb.id) not in s.colls
    assert resolve_scope(db_session, _p(ana), SearchParams(), facets=False).colls == {}
