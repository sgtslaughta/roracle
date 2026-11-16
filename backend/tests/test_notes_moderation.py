import uuid

import pytest
from api_seed import seed
from conftest import bearer
from sqlalchemy import select

from roracle.models import Annotation, AnnotationReport, AuditLog


@pytest.fixture
def doc(db_session, ana):
    d, v = seed(db_session, ana, visibility="everyone")
    db_session.commit()
    return d, v


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))


@pytest.fixture
def carol(make_token):
    return bearer(make_token(sub="carol", username="carol"))


def note(client, h, d, **kw):
    r = client.post(f"/documents/{d.id}/annotations", json={"body": "hi", **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def vote(client, h, nid, value):
    return client.put(f"/annotations/{nid}/vote", json={"value": value}, headers=h)


def test_vote_scores_and_clear(client, as_ana, as_bob, carol, ana, bob, doc):
    nid = note(client, as_ana, doc[0])
    r = vote(client, as_bob, nid, 1)
    assert r.status_code == 200
    assert r.json() == {"up": 1, "down": 0, "mine": 1, "score": pytest.approx(2 / 3)}
    r = vote(client, carol, nid, -1)
    assert r.json() == {"up": 1, "down": 1, "mine": -1, "score": 0.5}
    assert vote(client, as_bob, nid, -1).json()["up"] == 0  # change, not add
    r = vote(client, as_bob, nid, 0)
    assert r.json() == {"up": 0, "down": 1, "mine": 0, "score": pytest.approx(1 / 3)}
    assert vote(client, carol, nid, 0).json()["score"] == 0.5
    assert vote(client, as_bob, nid, 2).status_code == 422


def test_vote_unreadable_404_and_pat_403(client, as_ana, as_bob, ana, bob, doc):
    priv = note(client, as_ana, doc[0], visibility="private")
    assert vote(client, as_bob, priv, 1).status_code == 404
    tok = bearer(
        client.post("/me/tokens", json={"name": "c", "scopes": ["read", "write"]}, headers=as_ana).json()[
            "token"
        ]
    )
    nid = note(client, as_ana, doc[0])
    assert vote(client, tok, nid, 1).status_code == 403
    assert client.post(f"/annotations/{nid}/report", json={"reason": "spam"}, headers=tok).status_code == 403
    assert client.post(f"/annotations/{nid}/hide", json={"hidden": True}, headers=tok).status_code == 403


def test_report_idempotent(client, as_ana, as_bob, ana, bob, doc, db_session):
    nid = note(client, as_ana, doc[0])
    for _ in range(2):
        r = client.post(f"/annotations/{nid}/report", json={"reason": "spam"}, headers=as_bob)
        assert r.status_code == 204
    rows = db_session.scalars(select(AnnotationReport)).all()
    assert len(rows) == 1 and rows[0].reason == "spam"
    assert client.post(f"/annotations/{nid}/report", json={"reason": "x"}, headers=as_bob).status_code == 422
    priv = note(client, as_ana, doc[0], visibility="private")
    assert (
        client.post(f"/annotations/{priv}/report", json={"reason": "spam"}, headers=as_bob).status_code == 404
    )


def test_hide_by_owner_and_visibility(client, as_ana, as_bob, ana, bob, doc, admin, carol, db_session):
    d = doc[0]
    nid = note(client, as_bob, d)
    r = client.post(f"/annotations/{nid}/hide", json={"hidden": True}, headers=as_ana)
    assert r.status_code == 200 and r.json()["status"] == "hidden"
    assert client.get(f"/annotations/{nid}", headers=carol).status_code == 404
    for h in (as_ana, as_bob, admin):
        assert client.get(f"/annotations/{nid}", headers=h).json()["status"] == "hidden"
    r = client.post(f"/annotations/{nid}/hide", json={"hidden": False}, headers=admin)
    assert r.json()["status"] == "visible"
    assert client.get(f"/annotations/{nid}", headers=carol).status_code == 200
    acts = db_session.execute(select(AuditLog.action, AuditLog.target, AuditLog.detail)).all()
    assert [(a, t) for a, t, _ in acts] == [("annotation.hide", nid), ("annotation.unhide", nid)]
    assert all("body" not in dd for _, _, dd in acts)


def test_hide_forbidden_for_non_owner(client, as_ana, as_bob, ana, bob, doc, carol):
    nid = note(client, as_ana, doc[0])
    assert client.post(f"/annotations/{nid}/hide", json={"hidden": True}, headers=as_bob).status_code == 403
    priv = note(client, as_ana, doc[0], visibility="private")
    assert client.post(f"/annotations/{priv}/hide", json={"hidden": True}, headers=as_bob).status_code == 404


def test_admin_remove(client, as_ana, as_bob, ana, bob, doc, admin, db_session):
    d = doc[0]
    nid = note(client, as_bob, d)
    assert client.delete(f"/admin/annotations/{nid}", headers=as_ana).status_code == 403
    assert client.delete(f"/admin/annotations/{nid}", headers=admin).status_code == 204
    assert db_session.get(Annotation, uuid.UUID(nid)).status == "removed"
    for h in (as_ana, as_bob, admin):
        assert client.get(f"/annotations/{nid}", headers=h).status_code == 404
    assert client.delete(f"/admin/annotations/{nid}", headers=admin).status_code == 404
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "annotation.remove")).one()
    assert row.target == nid and row.detail == {"document_id": str(d.id)}
