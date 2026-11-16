"""T9: document soft delete + ownership transfer."""

import pytest
from api_seed import seed
from sqlalchemy import select

from roracle.api.routes.library import get_es
from roracle.models import AuditLog, Document, Job, Notification

pytestmark = pytest.mark.usefixtures("_blob_store_override")


class FES:
    def __init__(self):
        self.calls, self.dbq = [], []

    def update_by_query(self, **kw):
        self.calls.append(kw)

    def delete_by_query(self, **kw):
        self.dbq.append(kw)

    def search(self, **kw):
        return {"hits": {"hits": []}}


@pytest.fixture
def fes(app):
    f = FES()
    app.dependency_overrides[get_es] = lambda: f
    return f


def test_owner_deletes_document(client, as_ana, as_bob, ana, bob, db_session, fes):
    d, _ = seed(db_session, ana, visibility="everyone")
    assert client.get(f"/documents/{d.id}", headers=as_bob).status_code == 200
    assert client.delete(f"/documents/{d.id}", headers=as_bob).status_code == 403
    assert client.delete(f"/documents/{d.id}", headers=as_ana).status_code == 204
    assert fes.dbq and fes.dbq[0]["query"] == {"term": {"doc_id": str(d.id)}}
    for h in (as_ana, as_bob):
        assert client.get(f"/documents/{d.id}", headers=h).status_code == 404
    assert client.delete(f"/documents/{d.id}", headers=as_ana).status_code == 404
    db_session.expire_all()
    assert db_session.get(Document, d.id).deleted_at is not None
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "document.delete")) is not None
    assert db_session.scalars(select(Job).where(Job.kind == "acl_sync")).first() is not None


def test_admin_deletes_and_owner_notified(client, as_ana, adm, ana, db_session, fes):
    d, _ = seed(db_session, ana, visibility="private")
    assert client.delete(f"/documents/{d.id}", headers=adm).status_code == 204
    n = db_session.scalars(select(Notification).where(Notification.user_id == ana)).all()
    assert [x.kind for x in n] == ["document.deleted"]


def test_transfer(client, as_ana, as_bob, adm, ana, bob, db_session, fes):
    d, _ = seed(db_session, ana, visibility="private")
    body = {"user_id": str(bob)}
    assert client.post(f"/documents/{d.id}/transfer", headers=as_bob, json=body).status_code == 404
    assert (
        client.post(
            f"/documents/{d.id}/transfer",
            headers=as_ana,
            json={"user_id": "0" * 8 + "-0000-4000-8000-" + "0" * 12},
        ).status_code
        == 422
    )
    r = client.post(f"/documents/{d.id}/transfer", headers=as_ana, json=body)
    assert r.status_code == 200
    assert fes.calls[-1]["script"]["params"]["owner_id"] == str(bob)
    assert client.get(f"/documents/{d.id}", headers=as_ana).status_code == 404
    assert client.get(f"/documents/{d.id}", headers=as_bob).status_code == 200
    assert (
        client.post(f"/documents/{d.id}/transfer", headers=adm, json={"user_id": str(ana)}).status_code == 200
    )
    db_session.expire_all()
    assert db_session.get(Document, d.id).owner_id == ana
    kinds = {(x.user_id, x.kind) for x in db_session.scalars(select(Notification))}
    assert {(ana, "document.transferred"), (bob, "document.transferred")} <= kinds
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "document.transfer")) is not None
