"""Published-paper co-author grant (document_coauthors), PATCH removal, owner-only sharing fields,
directory lookups and GET /drafts/{id}/checks."""

import uuid

import pytest
from conftest import bearer
from sqlalchemy import select
from test_submit import built, mk, reports, sub  # noqa: F401

from roracle.access import visible_documents
from roracle.models import Document, DocumentCoauthor, DocumentShare, DocumentVersion
from roracle.models_writing import Draft

pytestmark = pytest.mark.usefixtures("_blob_store_override", "built")


@pytest.fixture(autouse=True)
def _null_es(app):
    """No real Elasticsearch in these route tests (a test may still override `get_es` itself)."""
    from es_fakes import NullEs

    from roracle.api.routes.library import get_es

    es = NullEs()
    app.dependency_overrides[get_es] = lambda: es
    return es


def publish(client, db, h, owner, co, vis="org_unit"):
    i = mk(client, h)
    client.put(f"/drafts/{i}/coauthors", headers=h, json={"users": [str(co)], "groups": ["/Elsewhere"]})
    reports(db, i, owner)
    assert sub(client, h, i, vis).status_code == 200
    for v in db.scalars(select(DocumentVersion)):
        v.stage = "searchable"
    db.commit()
    return i


def test_coauthor_keeps_access_org_unit(client, as_ana, ana, as_bob, bob, db_session, s3):
    i = publish(client, db_session, as_ana, ana, bob)
    doc = db_session.get(Document, uuid.UUID(i))
    assert doc.visibility == "org_unit" and doc.org_unit == "/Mathematics/Spectral Theory"
    rows = db_session.scalars(select(DocumentCoauthor)).all()
    assert [(r.document_id, r.user_id) for r in rows] == [(doc.id, bob)]  # users only, never groups
    assert db_session.scalars(select(DocumentShare)).all() == []
    assert client.get(f"/documents/{i}", headers=as_bob).status_code == 200
    # chat / library paths filter on visible_documents
    from roracle.auth import Principal

    pb = Principal(user_id=bob, sub="bob", username="bob", groups=("/Physics",), is_admin=False)
    assert doc.id in set(db_session.scalars(select(Document.id).where(visible_documents(pb))))
    # a stranger still cannot
    stranger = Principal(user_id=uuid.uuid4(), sub="s", username="s", groups=(), is_admin=False)
    assert doc.id not in set(db_session.scalars(select(Document.id).where(visible_documents(stranger))))


def test_patch_shared_no_revival_and_coauthor_stays(
    client, as_ana, ana, as_bob, bob, db_session, s3, _null_es
):
    i = publish(client, db_session, as_ana, ana, bob)
    assert client.patch(f"/documents/{i}", headers=as_ana, json={"visibility": "shared"}).status_code == 200
    assert db_session.scalars(select(DocumentShare)).all() == []
    assert _null_es.calls, "the PATCH must sync the ACL to ES"  # a dropped sync_acl call would fail here
    assert client.get(f"/documents/{i}", headers=as_bob).status_code == 200  # co-author grant, not a share


def test_owner_removes_coauthor(client, as_ana, ana, as_bob, bob, db_session, s3):
    i = publish(client, db_session, as_ana, ana, bob)
    assert client.patch(f"/documents/{i}", headers=as_bob, json={"coauthors": []}).status_code == 403
    r = client.patch(f"/documents/{i}", headers=as_ana, json={"coauthors": [str(uuid.uuid4())]})
    assert r.status_code == 422  # add is not allowed
    r = client.patch(f"/documents/{i}", headers=as_ana, json={"coauthors": []})
    assert r.status_code == 200 and r.json()["coauthors"] == []
    assert client.get(f"/documents/{i}", headers=as_bob).status_code == 404
    assert db_session.get(Draft, uuid.UUID(i)).coauthors["users"] == []


def test_sharing_fields_owner_admin_only(client, as_ana, ana, as_bob, bob, make_token, db_session, s3):
    i = publish(client, db_session, as_ana, ana, bob, vis="everyone")
    client.patch(
        f"/documents/{i}", headers=as_ana, json={"visibility": "shared", "share_groups": ["/Physics"]}
    )
    mine = client.get(f"/documents/{i}", headers=as_ana).json()
    assert mine["coauthors"] == [{"id": str(bob), "username": "bob"}]
    assert mine["share_groups"] == ["/Physics"] and mine["share_users"] == []
    other = client.get(f"/documents/{i}", headers=as_bob).json()
    assert not {"share_users", "share_groups", "coauthors"} & set(other)
    admin = bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))
    assert "coauthors" in client.get(f"/documents/{i}", headers=admin).json()


def test_directory_users(client, as_ana, ana, as_bob, bob, make_token):
    assert client.get("/directory/users", params={"q": "b"}, headers=as_ana).status_code == 422
    r = client.get("/directory/users", params={"q": "bo"}, headers=as_ana)
    assert r.json() == {"items": [{"id": str(bob), "username": "bob"}]}
    assert client.get("/directory/users", params={"q": "%%"}, headers=as_ana).json() == {"items": []}
    assert client.get("/directory/users", params={"q": "bo"}).status_code == 401


def test_directory_users_lookup(client, as_ana, ana, bob):
    r = client.get("/directory/users/lookup", params={"ids": [str(bob), str(uuid.uuid4())]}, headers=as_ana)
    assert r.json() == {"items": [{"id": str(bob), "username": "bob"}]}
    assert client.get("/directory/users/lookup", params={"ids": "nope"}, headers=as_ana).status_code == 422
    too_many = [str(uuid.uuid4()) for _ in range(101)]
    assert client.get("/directory/users/lookup", params={"ids": too_many}, headers=as_ana).status_code == 422
    assert client.get("/directory/users/lookup", params={"ids": [str(bob)]}).status_code == 401


def test_directory_groups(client, as_ana, ana, as_bob, bob):
    r = client.get("/directory/groups", params={"q": "phy"}, headers=as_ana)
    assert r.json() == {"items": ["/Physics"]}
    r = client.get("/directory/groups", params={"q": "spectral"}, headers=as_ana)
    assert r.json() == {"items": ["/Mathematics/Spectral Theory"]}
    assert client.get("/directory/groups", params={"q": "x"}, headers=as_ana).status_code == 422


def test_checks_are_callers_own(client, as_ana, ana, as_bob, bob, db_session):
    i = mk(client, as_ana)
    client.put(f"/drafts/{i}/coauthors", headers=as_ana, json={"users": [str(bob)]})
    assert client.get(f"/drafts/{i}/checks", headers=as_ana).json() == {
        "version": 1, "validation": None, "citations": None
    }  # fmt: skip
    reports(db_session, i, ana)
    mine = client.get(f"/drafts/{i}/checks", headers=as_ana).json()
    assert mine["validation"]["checked_by"] == str(ana) and mine["citations"]["ok"] is True
    theirs = client.get(f"/drafts/{i}/checks", headers=as_bob).json()
    assert theirs["validation"] is None and theirs["citations"] is None
    stranger = client.get(f"/drafts/{uuid.uuid4()}/checks", headers=as_bob)
    assert stranger.status_code == 404


def test_removing_coauthor_revokes_share(client, as_ana, ana, as_bob, bob, db_session, s3, app):
    from api_seed import FakeES
    from test_search_api import FakeLLM, hit

    from roracle.api.routes.library import get_es
    from roracle.api.routes.search import get_llm

    class UES(FakeES):
        def update_by_query(self, **kw):
            return {}

    fes = UES()
    app.dependency_overrides[get_es] = lambda: fes
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    i = publish(client, db_session, as_ana, ana, bob)
    r = client.patch(
        f"/documents/{i}", headers=as_ana, json={"visibility": "shared", "share_users": [str(bob)]}
    )
    assert r.status_code == 200
    r = client.patch(f"/documents/{i}", headers=as_ana, json={"coauthors": []})
    assert r.status_code == 200
    assert db_session.scalars(select(DocumentShare)).all() == []
    assert client.get(f"/documents/{i}", headers=as_bob).status_code == 404
    doc = db_session.get(Document, uuid.UUID(i))
    ver = db_session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)).first()
    fes.hits = [hit(doc, ver)]
    assert client.get("/search", headers=as_bob, params={"q": "alpha"}).json()["results"] == []
