import pytest
from api_seed import seed
from elasticsearch import TransportError
from sqlalchemy import select

from roracle.api.routes.library import get_es
from roracle.models import Annotation, DocumentVersion


class QES:
    def __init__(self, hits=None, err=None):
        self.hits, self.err, self.calls = hits or [], err, []

    def search(self, **kw):
        self.calls.append(kw)
        if self.err:
            raise self.err
        return {"hits": {"hits": self.hits}}


@pytest.fixture
def setup(client, as_ana, ana, db_session):
    d, v1 = seed(db_session, ana, visibility="everyone", stage="searchable")
    db_session.commit()
    return d, v1


def add_v2(db, d):
    v1 = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == d.id))
    v2 = DocumentVersion(
        document_id=d.id, version=2, blob_sha256=v1.blob_sha256, filename="p.pdf", content_type="application/pdf",
        size=1, stage="searchable",
    )  # fmt: skip
    v1.is_latest = False
    db.add(v2)
    db.commit()
    return v2


def make_note(client, h, d, **kw):
    r = client.post(f"/documents/{d.id}/annotations", json={"body": "b", **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def listing(client, app, h, d, es):
    app.dependency_overrides[get_es] = lambda: es
    r = client.get(f"/documents/{d.id}/annotations", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_found_moves_version_and_page(client, app, as_ana, setup, db_session):
    d, _ = setup
    nid = make_note(client, as_ana, d, anchor={"quote": "hello world", "page": 1})
    v2 = add_v2(db_session, d)
    es = QES(
        hits=[
            {
                "_source": {
                    "pages": [3],
                    "bboxes": [{"page": 3, "l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "BOTTOMLEFT"}],
                }
            }
        ]
    )
    (it,) = listing(client, app, as_ana, d, es)
    assert it["version_id"] == str(v2.id) and it["version"] == 2 and it["may_not_apply_to_version"] is None
    assert it["anchor"]["page"] == 3 and it["anchor"]["bbox"] == {
        "x0": 1,
        "y0": 2,
        "x1": 3,
        "y1": 4,
        "coord_origin": "BOTTOMLEFT",
    }
    q = es.calls[0]["query"]["bool"]
    assert {"term": {"version_id": str(v2.id)}} in q["filter"]
    assert q["must"] == [{"match_phrase": {"text": "hello world"}}]
    db_session.expire_all()  # persisted
    assert db_session.get(Annotation, __import__("uuid").UUID(nid)).version_id == v2.id
    listing(client, app, as_ana, d, es)
    assert len(es.calls) == 1  # already anchored: no lookup


def test_absent_flags_stale_and_no_repeat(client, app, as_ana, setup, db_session):
    d, _ = setup
    make_note(client, as_ana, d, anchor={"quote": "gone", "page": 1})
    add_v2(db_session, d)
    es = QES()
    (it,) = listing(client, app, as_ana, d, es)
    assert it["may_not_apply_to_version"] == 2 and it["version"] == 1
    listing(client, app, as_ana, d, es)
    assert len(es.calls) == 1  # stale for v2 already known


def test_es_error_leaves_untouched(client, app, as_ana, setup, db_session):
    d, v1 = setup
    make_note(client, as_ana, d, anchor={"quote": "q", "page": 1})
    add_v2(db_session, d)
    (it,) = listing(client, app, as_ana, d, QES(err=TransportError("down")))
    assert it["version_id"] == str(v1.id) and it["may_not_apply_to_version"] is None


def test_doc_level_note_just_moves(client, app, as_ana, setup, db_session):
    d, _ = setup
    make_note(client, as_ana, d)
    v2 = add_v2(db_session, d)
    es = QES()
    (it,) = listing(client, app, as_ana, d, es)
    assert it["version_id"] == str(v2.id) and es.calls == []


def test_cap_50_and_replies_untouched(client, app, as_ana, setup, db_session):
    d, _ = setup
    for _ in range(51):
        make_note(client, as_ana, d, anchor={"quote": "q"})
    add_v2(db_session, d)
    es = QES(hits=[{"_source": {}}])
    listing(client, app, as_ana, d, es)
    assert len(es.calls) == 50


def test_unpublished_newer_version_not_anchored_to(client, app, as_ana, setup, db_session):
    d, v1 = setup
    make_note(client, as_ana, d, anchor={"quote": "q", "page": 1})
    v2 = add_v2(db_session, d)
    v2.stage = "parsed"
    db_session.commit()
    es = QES()
    (it,) = listing(client, app, as_ana, d, es)
    assert it["version_id"] == str(v1.id) and it["may_not_apply_to_version"] is None and es.calls == []


def test_older_searchable_does_not_pull_back(client, app, as_ana, setup, db_session):
    d, v1 = setup
    v2 = add_v2(db_session, d)
    v1.stage = "parsed"
    nid = make_note(client, as_ana, d, anchor={"quote": "q"})  # lands on v2
    v2.stage = "parsed"
    v1.stage = "searchable"
    db_session.commit()
    es = QES()
    (it,) = listing(client, app, as_ana, d, es)
    assert it["id"] == nid and it["version_id"] == str(v2.id) and es.calls == []


def test_hit_without_pages_clears_page(client, app, as_ana, setup, db_session):
    d, _ = setup
    make_note(
        client, as_ana, d, anchor={"quote": "q", "page": 4, "bbox": {"x0": 1, "y0": 1, "x1": 2, "y1": 2}}
    )
    add_v2(db_session, d)
    (it,) = listing(client, app, as_ana, d, QES(hits=[{"_source": {}}]))
    assert it["anchor"]["page"] is None and it["anchor"]["bbox"] is None
