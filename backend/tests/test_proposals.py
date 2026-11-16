import uuid

import pytest
from api_seed import seed
from conftest import bearer
from sqlalchemy import select

from roracle import pats
from roracle.models import Annotation, AuditLog, Document, Proposal
from roracle.notes import proposals

NF = {"detail": "not found"}


@pytest.fixture
def doc(db_session, ana):
    d, v = seed(db_session, ana, visibility="everyone")
    db_session.commit()
    return d, v


@pytest.fixture
def as_pat(db_session, ana):
    _, token = pats.create(db_session, ana, "t", ["read", "write"], 30)
    db_session.commit()
    return bearer(token)


def prop(db_session, uid, doc_id, client="c1", **kw):
    from roracle.auth import Principal

    p = Principal(user_id=uid, sub="x", username="x", groups=(), is_admin=False, client=client, via="pat")
    row = proposals.propose(
        db_session, p, kw.pop("kind", "note"), {"document_id": str(doc_id), "body": "b", **kw}
    )
    db_session.commit()
    return row


def test_approve_creates_private_agent_assisted_note(client, as_ana, ana, doc, db_session):
    d, _ = doc
    row = prop(db_session, ana, d.id)
    r = client.post(f"/proposals/{row.id}/approve", headers=as_ana)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["agent_assisted"] is True and j["visibility"] == "private" and j["author"]["id"] == str(ana)
    db_session.refresh(row)
    assert row.status == "approved" and str(row.annotation_id) == j["id"]
    a = db_session.scalars(select(AuditLog).where(AuditLog.action == "proposal.approve")).one()
    assert "b" not in str(a.detail.get("body", ""))
    assert client.post(f"/proposals/{row.id}/approve", headers=as_ana).status_code == 409
    assert client.post(f"/proposals/{row.id}/reject", headers=as_ana).status_code == 409


def test_approve_with_edits(client, as_ana, ana, doc, db_session):
    row = prop(db_session, ana, doc[0].id)
    r = client.post(
        f"/proposals/{row.id}/approve", json={"body": "edited", "visibility": "everyone"}, headers=as_ana
    )
    assert r.json()["body"] == "edited" and r.json()["visibility"] == "everyone"


def test_reject_and_list(client, as_ana, ana, doc, db_session):
    a, b = prop(db_session, ana, doc[0].id), prop(db_session, ana, doc[0].id)
    assert client.post(f"/proposals/{a.id}/reject", headers=as_ana).status_code == 204
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "proposal.reject")).one()
    assert db_session.scalars(select(Annotation)).all() == []
    j = client.get("/me/proposals?status=pending", headers=as_ana).json()
    assert j["total"] == 1 and j["items"][0]["id"] == str(b.id)
    assert j["items"][0]["document"] == {"id": str(doc[0].id), "title": "Doc"}
    assert client.get("/me/proposals", headers=as_ana).json()["total"] == 2


def test_other_user_404(client, as_bob, as_ana, ana, bob, doc, db_session):
    row = prop(db_session, ana, doc[0].id)
    for path in ("approve", "reject"):
        r = client.post(f"/proposals/{row.id}/{path}", headers=as_bob)
        assert r.status_code == 404 and r.json() == NF
    assert client.get("/me/proposals", headers=as_bob).json() == {"total": 0, "items": []}
    assert client.post(f"/proposals/{uuid.uuid4()}/approve", headers=as_ana).status_code == 404


def test_cap_per_client(db_session, ana, doc):
    from fastapi import HTTPException

    for _ in range(50):
        prop(db_session, ana, doc[0].id)
    with pytest.raises(HTTPException) as e:
        prop(db_session, ana, doc[0].id)
    assert e.value.status_code == 429 and e.value.detail == "too many pending proposals"
    db_session.rollback()
    prop(db_session, ana, doc[0].id, client="other")


def test_doc_became_invisible_404(client, as_ana, as_bob, ana, bob, db_session):
    d, _ = seed(db_session, bob, visibility="everyone")
    db_session.commit()
    row = prop(db_session, ana, d.id)
    d.visibility = "private"
    db_session.commit()
    assert client.post(f"/proposals/{row.id}/approve", headers=as_ana).status_code == 404
    db_session.refresh(row)
    assert row.status == "pending"
    j = client.get("/me/proposals", headers=as_ana).json()
    assert j["items"][0]["document"] is None


def test_pat_cannot_decide(client, as_pat, ana, doc, db_session):
    row = prop(db_session, ana, doc[0].id)
    for path in (f"/proposals/{row.id}/approve", f"/proposals/{row.id}/reject"):
        assert client.post(path, headers=as_pat).status_code == 403
    assert client.post("/proposals/bulk-approve", json={"ids": []}, headers=as_pat).status_code == 403
    assert client.get("/me/proposals", headers=as_pat).status_code == 200


def test_bulk_mix(client, as_ana, ana, doc, db_session):
    good, gone = prop(db_session, ana, doc[0].id), prop(db_session, ana, doc[0].id)
    done = prop(db_session, ana, doc[0].id)
    client.post(f"/proposals/{done.id}/reject", headers=as_ana)
    missing = uuid.uuid4()
    r = client.post(
        "/proposals/bulk-approve", json={"ids": [str(good.id), str(done.id), str(missing)]}, headers=as_ana
    )
    j = r.json()
    assert [a["proposal_id"] for a in j["approved"]] == [str(good.id)]
    assert {f["proposal_id"] for f in j["failed"]} == {str(done.id), str(missing)}
    assert len(db_session.scalars(select(Annotation)).all()) == 1
    assert (
        client.post(
            "/proposals/bulk-approve", json={"ids": [str(uuid.uuid4())] * 101}, headers=as_ana
        ).status_code
        == 422
    )
    assert gone.status == "pending"


def test_propose_validates_like_create(db_session, ana, doc):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        prop(db_session, ana, doc[0].id, kind="relation")
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        prop(db_session, ana, uuid.uuid4())
    assert e.value.status_code == 404
    assert db_session.scalars(select(Proposal)).all() == []


def test_reply_proposal(client, as_ana, ana, doc, db_session):
    root = client.post(f"/documents/{doc[0].id}/annotations", json={"body": "r"}, headers=as_ana).json()["id"]
    row = prop(db_session, ana, doc[0].id, parent_id=root)
    j = client.post(f"/proposals/{row.id}/approve", headers=as_ana).json()
    assert j["parent_id"] == root and j["agent_assisted"] is True
    assert db_session.scalars(select(Document)).all()


def test_reply_edit_visibility_422(client, as_ana, ana, doc, db_session):
    root = client.post(f"/documents/{doc[0].id}/annotations", json={"body": "r"}, headers=as_ana).json()["id"]
    row = prop(db_session, ana, doc[0].id, parent_id=root)
    r = client.post(f"/proposals/{row.id}/approve", json={"visibility": "private"}, headers=as_ana)
    assert r.status_code == 422


def test_bulk_corrupt_payload_fails_softly(client, as_ana, ana, doc, db_session):
    row = prop(db_session, ana, doc[0].id)
    row.payload = {"document_id": str(doc[0].id), "body": "", "kind": "bogus"}
    db_session.commit()
    j = client.post("/proposals/bulk-approve", json={"ids": [str(row.id)]}, headers=as_ana).json()
    assert j["approved"] == [] and j["failed"][0]["proposal_id"] == str(row.id)
