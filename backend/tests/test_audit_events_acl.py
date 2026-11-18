"""Sharing, visibility, upload and chat-share changes are audited (P15 task 6). Counts only, never ids/names."""

import pytest
from api_seed import seed
from sqlalchemy import select
from test_chat_api import world  # noqa: F401
from test_submit import built, mk  # noqa: F401

from roracle import search_index
from roracle.api.routes.library import get_es
from roracle.db import get_session
from roracle.models import AuditLog

pytestmark = pytest.mark.usefixtures("_blob_store_override")


class FES:
    def update_by_query(self, **kw):
        pass

    def search(self, **kw):
        return {"hits": {"hits": []}}


@pytest.fixture(autouse=True)
def _es(app):
    app.dependency_overrides[get_es] = lambda: FES()


def rows(db, action):
    db.rollback()
    return list(db.scalars(select(AuditLog).where(AuditLog.action == action)))


def test_patch_visibility_audited(client, as_ana, ana, db_session):
    d, _ = seed(db_session, ana, visibility="private")
    assert (
        client.patch(f"/documents/{d.id}", headers=as_ana, json={"visibility": "everyone"}).status_code == 200
    )
    (r,) = rows(db_session, "document.acl")
    assert r.target == str(d.id)
    assert r.detail == {
        "fields": ["visibility"], "visibility": "everyone", "org_unit": d.org_unit,
        "share_users": 0, "share_groups": 0,
    }  # fmt: skip


def test_patch_title_only_not_audited(client, as_ana, ana, db_session):
    d, _ = seed(db_session, ana, visibility="private")
    assert client.patch(f"/documents/{d.id}", headers=as_ana, json={"title": "New"}).status_code == 200
    assert rows(db_session, "document.acl") == []


def test_patch_share_counts(client, as_ana, ana, bob, db_session):
    d, _ = seed(db_session, ana, visibility="shared")
    body = {"share_users": [str(bob)], "share_groups": ["/A", "/B"]}
    assert client.patch(f"/documents/{d.id}", headers=as_ana, json=body).status_code == 200
    (r,) = rows(db_session, "document.acl")
    assert r.detail["fields"] == ["share_groups", "share_users"]
    assert (r.detail["share_users"], r.detail["share_groups"]) == (1, 2)
    assert str(bob) not in str(r.detail)


def test_patch_503_leaves_no_row(app, client, as_ana, ana, db_session, monkeypatch):
    d, _ = seed(db_session, ana, visibility="private")
    db_session.commit()  # the real session below is a second connection: it must not wait on our open txn
    app.dependency_overrides.pop(get_session)  # real session: rollback + replay
    monkeypatch.setattr(search_index, "sync_acl", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("es")))
    r = client.patch(f"/documents/{d.id}", headers=as_ana, json={"visibility": "everyone"})
    assert r.status_code == 503
    assert rows(db_session, "document.acl") == []


def test_upload_audited_without_filename(client, as_ana, db_session):
    r = client.post(
        "/documents", headers=as_ana, files={"file": ("secret-name.pdf", b"%PDF-1 hi", "application/pdf")}
    )
    assert r.status_code in (200, 201, 202), r.text
    (row,) = rows(db_session, "document.upload")
    assert row.target == r.json()["document_id"]
    assert row.detail == {"version_id": r.json()["version_id"], "size": 9}
    assert "secret-name" not in str(row.detail)


def test_chat_share_counts(client, as_ana, ana, bob, world, db_session):  # noqa: F811
    cid = world["chat"].id
    body = {"users": [str(bob), str(ana)], "groups": ["/Team", "/Team"]}
    assert client.put(f"/chats/{cid}/shares", headers=as_ana, json=body).status_code == 200
    (r,) = rows(db_session, "chat.share")
    assert r.target == str(cid) and r.detail == {"users": 1, "groups": 1}


@pytest.mark.usefixtures("built")
def test_put_coauthors_audited(client, as_ana, bob, db_session):
    i = mk(client, as_ana)
    r = client.put(f"/drafts/{i}/coauthors", headers=as_ana, json={"users": [str(bob)], "groups": ["/X"]})
    assert r.status_code == 200
    db_session.commit()  # the route leaves commit to get_session, which the test override skips
    (row,) = rows(db_session, "document.acl")
    assert row.detail["fields"] == ["coauthors"] and row.detail["visibility"] == "shared"
    assert (row.detail["share_users"], row.detail["share_groups"]) == (1, 1)
