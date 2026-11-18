"""`manage` PAT scope: sharing, delete, transfer, submit drafts, wide handoffs (P15 task 3)."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from api_seed import seed
from conftest import bearer
from sqlalchemy import text

from roracle import pats
from roracle.api.routes.library import get_es
from roracle.auth import Principal, as_agent
from roracle.models import PersonalAccessToken

pytestmark = pytest.mark.usefixtures("_blob_store_override")


class FES:
    def update_by_query(self, **kw): ...
    def delete_by_query(self, **kw): ...
    def search(self, **kw):
        return {"hits": {"hits": []}}


@pytest.fixture(autouse=True)
def _es(app):
    app.dependency_overrides[get_es] = lambda: FES()


def new_pat(db_session, uid, scopes):
    _, token = pats.create(db_session, uid, "t", scopes, 30)
    db_session.commit()
    return bearer(token)


def create(client, headers, scopes):
    r = client.post("/me/tokens", json={"name": "c", "scopes": scopes}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def doc(db_session, ana):
    return seed(db_session, ana, visibility="private")[0]


@pytest.mark.parametrize(
    "call",
    [
        lambda c, h, d: c.patch(f"/documents/{d.id}", json={"visibility": "everyone"}, headers=h),
        lambda c, h, d: c.delete(f"/documents/{d.id}", headers=h),
        lambda c, h, d: c.post(f"/documents/{d.id}/transfer", json={"user_id": str(uuid4())}, headers=h),
    ],
)
def test_write_pat_cannot_manage(client, db_session, ana, doc, call):
    h = new_pat(db_session, ana, ["read", "write"])
    r = call(client, h, doc)
    assert r.status_code == 403 and r.json()["detail"] == "insufficient scope: manage"
    db_session.refresh(doc)
    assert doc.deleted_at is None and doc.visibility == "private"


def test_write_pat_can_retitle(client, db_session, ana, doc):
    h = new_pat(db_session, ana, ["read", "write"])
    r = client.patch(f"/documents/{doc.id}", json={"title": "New"}, headers=h)
    assert r.status_code == 200, r.text


def test_write_pat_cannot_submit_draft(client, db_session, ana):
    h = new_pat(db_session, ana, ["read", "write"])
    r = client.post(f"/drafts/{uuid4()}/submit", json={"visibility": "private"}, headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "insufficient scope: manage"


def test_write_pat_cannot_share_chat(client, db_session, ana):
    h = new_pat(db_session, ana, ["read", "write"])
    r = client.put(f"/chats/{uuid4()}/shares", json={"users": [], "groups": []}, headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "insufficient scope: manage"


def test_write_pat_handoff_creator_only(client, db_session, ana):
    h = new_pat(db_session, ana, ["read", "write"])
    body = {"kind": "agent", "title": "t", "brief": "b"}
    r = client.post("/handoffs", json={**body, "audience": "creator"}, headers=h)
    assert r.status_code == 201, r.text
    r = client.post("/handoffs", json={**body, "audience": "authenticated"}, headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "insufficient scope: manage"
    code = client.post("/handoffs", json=body, headers=h).json()["code"]
    r = client.patch(f"/handoffs/{code}", json={"audience": "authenticated"}, headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "insufficient scope: manage"


def test_scope_check_precedes_lookup(client, db_session, ana):
    h = new_pat(db_session, ana, ["read", "write"])
    r = client.delete(f"/documents/{uuid4()}", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "insufficient scope: manage"


def test_manage_pat_can_delete(client, db_session, ana, doc):
    h = new_pat(db_session, ana, ["read", "write", "manage"])
    assert client.delete(f"/documents/{doc.id}", headers=h).status_code == 204


def test_jwt_can_delete(client, as_ana, doc):
    assert client.delete(f"/documents/{doc.id}", headers=as_ana).status_code == 204


def test_token_scopes_closure(client, as_ana):
    assert create(client, as_ana, ["manage"])["scopes"] == ["manage", "read", "write"]
    assert create(client, as_ana, ["write"])["scopes"] == ["read", "write"]
    assert create(client, as_ana, ["read"])["scopes"] == ["read"]


def test_backfill_sql_grants_manage_to_write_tokens(db_session, user):
    path = Path(__file__).parents[1] / "migrations/versions/0021_pat_manage_scope.py"
    spec = importlib.util.spec_from_file_location("m0021", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for i, scopes in enumerate((["read"], ["read", "write"])):
        _, _ = pats.create(db_session, user.id, f"t{i}", scopes, 30)
    db_session.flush()

    def got():
        rows = db_session.query(PersonalAccessToken).order_by(PersonalAccessToken.name).all()
        return [sorted(r.scopes) for r in rows]

    for _ in range(2):  # second run is a no-op
        db_session.execute(text(mod.BACKFILL_SQL))
        db_session.expire_all()
        assert got() == [["read"], ["manage", "read", "write"]]


def test_mcp_principal_has_no_manage():
    p = Principal(user_id=uuid4(), sub="s", username="u", groups=(), is_admin=False)
    assert "manage" in p.scopes
    assert as_agent(p).scopes == {"read", "write"}
