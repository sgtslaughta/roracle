"""T7/C1: shares grant access only while visibility == "shared"; PATCH to any other visibility drops them."""

import json

import pytest
from api_seed import FakeES, seed
from fastapi.testclient import TestClient
from mcp_helpers import call
from sqlalchemy import select
from test_chat_retrieve import FakeES as ChatES
from test_chat_retrieve import hit as chit
from test_chat_retrieve import run
from test_handoff import _chat, mk
from test_search_api import FakeLLM, hit

from roracle.access import readable_documents, visible_documents
from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.auth import Principal
from roracle.models import Document, DocumentShare, Job

pytestmark = pytest.mark.usefixtures("_blob_store_override")


@pytest.fixture
def fes(app):
    f = FakeES()
    app.dependency_overrides[get_es] = lambda: f
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    return f


def _stale(db, ana, bob, vis, **kw):
    """Doc whose share rows (user bob + group /Physics) survived a visibility change."""
    d, v = seed(db, ana, visibility=vis, **kw)
    db.add_all(
        [
            DocumentShare(document_id=d.id, principal_kind="user", principal=str(bob)),
            DocumentShare(document_id=d.id, principal_kind="group", principal="/Physics"),
        ]
    )
    db.commit()
    return d, v


@pytest.mark.parametrize("vis", ["private", "org_unit"])
def test_sql_shares_inert_unless_shared(db_session, ana, bob, vis):
    d, _ = _stale(db_session, ana, bob, vis, org_unit="/Zoology")
    p = Principal(bob, "bob", "bob", ("/Physics",), False)
    for rule in (visible_documents, readable_documents):
        assert d.id not in set(db_session.scalars(select(Document.id).where(rule(p))))
    d.visibility = "shared"
    db_session.flush()
    for rule in (visible_documents, readable_documents):
        assert d.id in set(db_session.scalars(select(Document.id).where(rule(p))))


def test_stale_shares_blocked_detail_file_search_mcp(client, as_bob, ana, bob, db_session, fes):
    d, v = _stale(db_session, ana, bob, "private")
    assert client.get(f"/documents/{d.id}", headers=as_bob).status_code == 404
    assert client.get(f"/documents/{d.id}/versions/1/file", headers=as_bob).status_code == 404
    fes.hits = [hit(d, v)]
    assert client.get("/search", headers=as_bob, params={"q": "alpha"}).json()["results"] == []
    with TestClient(client.app) as mc:
        err, out = call(mc, as_bob, "search", query="alpha", limit=5)
    assert not err and out["results"] == []


def test_stale_shares_blocked_chat_retrieval(db_session, settings, ana, bob):
    d, v = _stale(db_session, ana, bob, "private")
    me = Principal(bob, "bob", "bob", ("/Physics",), False)
    assert run(db_session, ChatES([chit(d, v)]), settings, me) == []


def test_stale_shares_handoff_redacts(client, as_ana, as_bob, ana, bob, db_session):
    pub = seed(db_session, ana, title="PubTitle", visibility="everyone")
    priv = _stale(db_session, ana, bob, "private", title="SecretTitle")
    chat = _chat(db_session, ana, priv, pub)
    c = mk(client, as_ana, kind="chat", source_id=str(chat.id), audience="authenticated")
    t = client.get(f"/handoffs/{c['code']}/parts/transcript", headers=as_bob).json()
    m = client.get(f"/handoffs/{c['code']}", headers=as_bob).json()
    blob = json.dumps(m) + json.dumps(t)
    assert "SecretTitle" not in blob and str(priv[0].id) not in blob and m["withheld"] == 1


class _UbqES:
    def __init__(self):
        self.calls = []

    def update_by_query(self, **kw):
        self.calls.append(kw)


def test_patch_to_private_drops_shares_and_revokes(app, client, as_ana, as_bob, ana, bob, db_session):
    fes = _UbqES()
    app.dependency_overrides[get_es] = lambda: fes
    d, _ = seed(db_session, ana, visibility="private")
    r = client.patch(
        f"/documents/{d.id}", headers=as_ana,
        json={"visibility": "shared", "share_users": [str(bob)], "share_groups": ["/Physics"]},
    )  # fmt: skip
    assert r.status_code == 200 and client.get(f"/documents/{d.id}", headers=as_bob).status_code == 200
    db_session.execute(Job.__table__.delete())
    db_session.commit()
    for vis in ("private", "org_unit", "everyone"):
        client.patch(
            f"/documents/{d.id}", headers=as_ana, json={"visibility": "shared", "share_users": [str(bob)]}
        )
        assert (
            client.patch(
                f"/documents/{d.id}", headers=as_ana, json={"visibility": vis, "org_unit": "/Physics"}
            ).status_code
            == 200
        )
        assert db_session.scalars(select(DocumentShare).where(DocumentShare.document_id == d.id)).all() == []
        assert db_session.scalars(select(Job).where(Job.kind == "acl_sync")).first() is not None
    client.patch(f"/documents/{d.id}", headers=as_ana, json={"visibility": "private"})
    assert client.get(f"/documents/{d.id}", headers=as_bob).status_code == 404
    assert fes.calls[-1]["script"]["params"]["share_users"] == []
