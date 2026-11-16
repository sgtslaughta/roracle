import uuid

import pytest
from api_seed import FakeES, seed
from conftest import bearer
from sqlalchemy import select
from test_community_notes import STRICT, note, vote

from roracle import pats
from roracle.api.routes.library import get_es
from roracle.auth import Principal
from roracle.models import Annotation, AuditLog, Notification, Proposal
from roracle.models_labels import Label
from roracle.notes import proposals
from roracle.notes.community import community_notes

pytestmark = pytest.mark.usefixtures("_blob_store_override")


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


@pytest.fixture(autouse=True)
def fes(app):
    f = FakeES()
    app.dependency_overrides[get_es] = lambda: f
    return f


def mk(client, admin, name, rank, **gates):
    r = client.post(
        "/admin/labels",
        json={"name": name, "colour": "#aabbcc", "rank": rank, "gates": gates},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def general(db):
    return str(db.scalar(select(Label.id).where(Label.name == "General")))


def doc_label(db, d, lid):
    d.label_id = lid
    db.commit()


def mknote(client, h, d, **kw):
    r = client.post(f"/documents/{d.id}/annotations", json={"body": "hi", "kind": "finding", **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def put(client, h, nid, label, **kw):
    return client.put(f"/annotations/{nid}/label", headers=h, json={"label_id": label, **kw})


@pytest.fixture
def world(db_session, ana):
    d, v = seed(db_session, ana, visibility="everyone")
    db_session.commit()
    return d, v


def test_new_note_inherits_doc_label(client, as_ana, admin, world, db_session):
    d, _ = world
    sec = mk(client, admin, "Secret", 5)
    doc_label(db_session, d, sec)
    n = mknote(client, as_ana, d)
    assert n["label"] is None and n["effective_label"]["name"] == "Secret"


def test_create_with_label_and_below_doc_422(client, as_ana, admin, world, db_session):
    d, _ = world
    mid, hi = mk(client, admin, "Mid", 3), mk(client, admin, "High", 6)
    n = mknote(client, as_ana, d, label_id=mid)
    assert n["label"]["name"] == "Mid" and n["effective_label"]["name"] == "Mid"
    doc_label(db_session, d, hi)
    r = client.post(f"/documents/{d.id}/annotations", json={"body": "x", "label_id": mid}, headers=as_ana)
    assert r.status_code == 422


def test_author_raises_lowers_403_notified_audited(
    client, as_ana, as_bob, ana, bob, admin, world, db_session
):
    d, _ = world
    mid, hi = mk(client, admin, "Mid", 3), mk(client, admin, "High", 6)
    n = mknote(client, as_ana, d)
    r = put(client, as_ana, n["id"], hi)
    assert r.status_code == 200 and r.json()["effective_label"]["name"] == "High"
    assert put(client, as_ana, n["id"], mid).status_code == 403
    assert put(client, as_ana, n["id"], None).status_code == 403  # inherit = lowering
    assert put(client, as_bob, n["id"], hi).status_code == 403  # not the author (note is readable)
    a = [x.detail for x in db_session.scalars(select(AuditLog).where(AuditLog.action == "annotation.label"))]
    assert a == [{"from": general(db_session), "to": hi, "via": "user"}]
    assert not db_session.scalars(select(Notification).where(Notification.kind == "label.changed")).all()


def test_admin_lowers_with_reason_notifies_author(client, as_ana, ana, admin, world, db_session):
    d, _ = world
    hi = mk(client, admin, "High", 6, reason_on_lower=True)
    n = mknote(client, as_ana, d, label_id=hi)
    assert put(client, admin, n["id"], None).status_code == 422  # reason required
    r = put(client, admin, n["id"], None, reason="declassified")
    assert r.status_code == 200 and r.json()["label"] is None
    assert r.json()["effective_label"]["name"] == "General"
    nt = db_session.scalars(select(Notification).where(Notification.kind == "label.changed")).one()
    assert nt.user_id == ana and nt.payload["annotation_id"] == n["id"]


def test_admin_lower_below_doc_422(client, as_ana, admin, world, db_session):
    d, _ = world
    mid, hi = mk(client, admin, "Mid", 3), mk(client, admin, "High", 6)
    n = mknote(client, as_ana, d, label_id=hi)
    doc_label(db_session, d, mid)
    assert put(client, admin, n["id"], general(db_session)).status_code == 422


def test_raising_doc_above_note_makes_doc_effective(client, as_ana, admin, world, db_session):
    d, _ = world
    mid, hi = mk(client, admin, "Mid", 3), mk(client, admin, "High", 6)
    n = mknote(client, as_ana, d, label_id=mid)
    doc_label(db_session, d, hi)
    j = client.get(f"/annotations/{n['id']}", headers=as_ana).json()
    assert j["label"]["name"] == "Mid" and j["effective_label"]["name"] == "High"


def test_pat_and_replies_and_404(client, as_ana, ana, admin, world, db_session):
    d, _ = world
    hi = mk(client, admin, "High", 6)
    n = mknote(client, as_ana, d)
    _, tok = pats.create(db_session, ana, "t", ["read", "write"], 30)
    db_session.commit()
    r = put(client, bearer(tok), n["id"], hi)
    assert r.status_code == 403 and r.json() == {"detail": "agents must propose"}
    rep = client.post(f"/annotations/{n['id']}/replies", json={"body": "r"}, headers=as_ana).json()
    assert put(client, as_ana, rep["id"], hi).status_code == 422
    assert put(client, as_ana, str(uuid.uuid4()), hi).status_code == 404


def test_pat_list_annotations_omits_agent_blocked_effective_label(
    client, as_ana, ana, admin, world, db_session
):
    d, _ = world
    blk = mk(client, admin, "NoAgent", 4, block_agents=True)
    plain = mknote(client, as_ana, d, body="plain")
    hidden = mknote(client, as_ana, d, body="hidden", label_id=blk)
    reply = client.post(f"/annotations/{hidden['id']}/replies", json={"body": "r"}, headers=as_ana).json()
    _, tok = pats.create(db_session, ana, "t", ["read"], 30)
    db_session.commit()
    h = bearer(tok)
    ids = {x["id"] for x in client.get(f"/documents/{d.id}/annotations", headers=h).json()["items"]}
    assert ids == {plain["id"]}
    assert client.get(f"/annotations/{hidden['id']}", headers=h).status_code == 404
    assert client.get(f"/annotations/{reply['id']}", headers=h).status_code == 404
    jwt = {x["id"] for x in client.get(f"/documents/{d.id}/annotations", headers=as_ana).json()["items"]}
    assert jwt >= {plain["id"], hidden["id"], reply["id"]}
    # doc-level block hides every note from PATs
    doc_label(db_session, d, blk)
    assert client.get(f"/documents/{d.id}/annotations", headers=h).status_code == 404


def test_community_note_excluded_from_llm_but_in_search_hits(db_session, ana, bob, admin, client):
    d, v = seed(db_session, bob, title="Doc")
    xl = mk(client, admin, "NoLLM", 4, exclude_llm=True)
    ok = note(db_session, d, v, bob, body="fine")
    bad = note(db_session, d, v, bob, body="secret", n=1, label_id=xl)
    pr = Principal(ana, "ana", "researcher", (), False)
    from roracle.models import User

    voters = []
    for i in range(3):
        u = User(sub=f"v{i}", username=f"v{i}")
        db_session.add(u)
        db_session.flush()
        voters.append(u.id)
    vote(db_session, ok, voters)
    vote(db_session, bad, voters)
    db_session.commit()
    strict = community_notes(db_session, pr, {str(d.id): {2}}, strict=STRICT)[str(d.id)]
    assert [n["body"] for n in strict] == ["fine"]
    disp = community_notes(db_session, pr, {str(d.id): {2}})[str(d.id)]  # search display unaffected
    assert {n["body"] for n in disp} == {"fine", "secret"}


def test_community_note_excluded_when_doc_label_excludes(db_session, ana, bob, admin, client):
    d, v = seed(db_session, bob, title="Doc")
    xl = mk(client, admin, "NoLLM", 4, exclude_llm=True)
    note(db_session, d, v, ana, body="mine")
    doc_label(db_session, d, xl)
    pr = Principal(ana, "ana", "researcher", (), False)
    assert community_notes(db_session, pr, {str(d.id): {2}}, strict=STRICT) == {}


def test_evidence_download_blocked_by_note_label(client, as_ana, as_bob, admin, world, s3):
    from test_evidence import post

    d, _ = world
    nb = mk(client, admin, "NoExport", 3, block_export=True)
    n = mknote(client, as_ana, d)
    ev = post(client, as_ana, n["id"]).json()["id"]
    assert client.get(f"/evidence/{ev}/file", headers=as_bob).status_code != 403
    assert put(client, as_ana, n["id"], nb).status_code == 200
    r = client.get(f"/evidence/{ev}/file", headers=as_bob)
    assert r.status_code == 403 and r.json()["label"] == "NoExport"
    assert client.get(f"/evidence/{ev}/file", headers=as_ana).status_code != 403  # owner exempt


# ---------------- label proposals ----------------
def agent(uid, client="c1"):
    return Principal(user_id=uid, sub="x", username="x", groups=(), is_admin=False, client=client, via="pat")


def lprop(db, uid, d, label, **kw):
    row = proposals.propose(db, agent(uid), "label", {"document_id": str(d.id), "label_id": label, **kw})
    db.commit()
    return row


def test_label_proposal_approve_doc_and_note(client, as_ana, ana, admin, world, db_session):
    d, _ = world
    hi = mk(client, admin, "High", 6)
    n = mknote(client, as_ana, d)
    r1 = lprop(db_session, ana, d, hi)
    r2 = lprop(db_session, ana, d, hi, annotation_id=n["id"])
    assert client.post(f"/proposals/{r2.id}/approve", headers=as_ana).status_code == 200
    assert client.post(f"/proposals/{r1.id}/approve", headers=as_ana).status_code == 200
    db_session.refresh(d)
    assert str(d.label_id) == hi
    assert db_session.get(Annotation, uuid.UUID(n["id"])).label_id == uuid.UUID(hi)
    vias = [
        x.detail["via"] for x in db_session.scalars(select(AuditLog).where(AuditLog.action.like("%.label")))
    ]
    assert vias == ["proposal", "proposal"]


def test_label_proposal_reject_and_validation(client, as_ana, ana, admin, world, db_session):
    d, _ = world
    hi = mk(client, admin, "High", 6)
    row = lprop(db_session, ana, d, hi)
    assert client.post(f"/proposals/{row.id}/reject", headers=as_ana).status_code == 204
    db_session.refresh(d)
    assert str(d.label_id) == general(db_session)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        lprop(db_session, ana, d, str(uuid.uuid4()))
    assert e.value.status_code == 422


def test_owner_cannot_approve_lowering_admin_with_reason_can(
    client, as_ana, ana, admin, world, db_session, make_token
):
    d, _ = world
    hi = mk(client, admin, "High", 6, reason_on_lower=True)
    doc_label(db_session, d, hi)
    row = lprop(db_session, ana, d, general(db_session))
    r = client.post(f"/proposals/{row.id}/approve", headers=as_ana)
    assert r.status_code == 403
    db_session.refresh(row)
    assert row.status == "pending"
    # an admin owns the proposal when their own agent proposed it
    from roracle.models import User

    root = db_session.scalar(select(User.id).where(User.username == "root"))
    assert root is not None  # created by the admin token above
    row2 = lprop(db_session, root, d, general(db_session))
    assert client.post(f"/proposals/{row2.id}/approve", headers=admin).status_code == 422
    r = client.post(f"/proposals/{row2.id}/approve", json={"reason": "declassified"}, headers=admin)
    assert r.status_code == 200, r.text
    db_session.refresh(d)
    assert str(d.label_id) == general(db_session)


def test_agent_note_proposal_ignores_label(client, as_ana, ana, admin, world, db_session):
    d, _ = world
    hi = mk(client, admin, "High", 6)
    row = proposals.propose(
        db_session, agent(ana), "note", {"document_id": str(d.id), "body": "b", "label_id": hi}
    )
    assert "label_id" not in row.payload
    assert db_session.scalars(select(Proposal)).one()
