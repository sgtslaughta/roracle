# ruff: noqa: F811
"""Final whole-branch review fixes: stored per-user state must not leak gated content to PATs."""

import json

import pytest
from api_seed import seed
from conftest import bearer
from sqlalchemy import select
from test_labels_text import chat_with, cite_of, label, mk
from test_mcp_tools import fes, llm, mc, pat  # noqa: F401

from roracle.models import Chat, ChatMessage, Document, Notification, Proposal

pytestmark = pytest.mark.usefixtures("_blob_store_override")


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


def test_chat_viewer_not_returned_to_pat(client, as_ana, ana, admin, db_session):
    sec = mk(client, admin, "NoLLM", 3, exclude_llm=True)
    d, v = seed(db_session, ana, title="Locked")
    label(db_session, d, sec)
    c = chat_with(db_session, ana, d, v)
    m = db_session.scalars(
        select(ChatMessage).where(ChatMessage.chat_id == c.id, ChatMessage.role == "user")
    ).one()
    m.viewer = {"tabs": [{"doc_id": str(d.id), "page": 1}], "ignored": [],
                "selection": {"doc_id": str(d.id), "page": 1, "text": "SECRET-SELECTION"}}  # fmt: skip
    db_session.commit()
    tok = pat(db_session, ana, ["read"])
    assert "SECRET-SELECTION" not in client.get(f"/chats/{c.id}", headers=tok).text
    assert "SECRET-SELECTION" in client.get(f"/chats/{c.id}", headers=as_ana).text


def test_chat_pins_of_blocked_docs_hidden_from_pat(client, as_ana, ana, admin, db_session):
    sec = mk(client, admin, "NoAgent", 3, block_agents=True)
    d, _ = seed(db_session, ana, title="Hidden")
    ok, _ = seed(db_session, ana, title="Plain")
    label(db_session, d, sec)
    c = Chat(owner_id=ana, title="t", scope="all", pins=[str(d.id), str(ok.id)], excludes=[str(d.id)])
    db_session.add(c)
    db_session.commit()
    tok = pat(db_session, ana, ["read"])
    got = client.get(f"/chats/{c.id}", headers=tok).json()
    assert got["pins"] == [str(ok.id)] and got["excludes"] == []
    assert client.get(f"/chats/{c.id}", headers=as_ana).json()["pins"] == [str(d.id), str(ok.id)]
    wtok = pat(db_session, ana, ["read", "write"])
    r = client.patch(f"/chats/{c.id}", headers=wtok, json={"title": "t2"})
    assert r.status_code == 200 and r.json()["pins"] == [str(ok.id)] and r.json()["excludes"] == []


def test_note_label_exclude_llm_withholds_note_citation(client, as_ana, ana, admin, db_session):
    sec = mk(client, admin, "NoLLM", 3, exclude_llm=True)
    d, v = seed(db_session, ana, title="Plain")
    r = client.post(f"/documents/{d.id}/annotations",
                    json={"kind": "comment", "body": "n", "visibility": "everyone"}, headers=as_ana)  # fmt: skip
    nid = r.json()["id"]
    assert client.put(f"/annotations/{nid}/label", json={"label_id": sec}, headers=as_ana).status_code == 200
    c = chat_with(db_session, ana, d, v)
    m = db_session.scalars(
        select(ChatMessage).where(ChatMessage.chat_id == c.id, ChatMessage.role == "assistant")
    ).one()
    m.citations = [cite_of(d, v, note_id=nid)]
    m.retrieved = [{**cite_of(d, v, note_id=nid), "text": "SECRET-TEXT"}]
    db_session.commit()
    tok = pat(db_session, ana, ["read"])
    assert "SECRET" not in client.get(f"/chats/{c.id}", headers=tok).text


# ---- I2: draft text through checks endpoints ----
@pytest.fixture
def locked_draft(client, as_ana, ana, admin, db_session):
    sec = mk(client, admin, "NoLLM", 3, exclude_llm=True)
    did = client.post(
        "/drafts", json={"title": "D", "content": "A claim [@x]. SECRET-DRAFT"}, headers=as_ana
    ).json()["id"]
    import uuid

    label(db_session, db_session.get(Document, uuid.UUID(did)), sec)
    return did


def test_pat_gets_no_draft_text_via_checks(client, ana, db_session, locked_draft):
    tok = pat(db_session, ana, ["read", "write"])
    for method, url in (("get", "/checks"), ("post", "/check-citations"), ("post", "/validate")):
        r = getattr(client, method)(f"/drafts/{locked_draft}{url}", headers=tok)
        assert r.status_code == 403, url


def test_mcp_check_citations_validate_blocked(mc, ana, db_session, fes, locked_draft):
    from mcp_helpers import call

    h = pat(db_session, ana, ["read", "write"])
    for tool in ("check_citations", "validate_draft"):
        err, text = call(mc, h, tool, draft_id=locked_draft)
        assert err and "withheld" in text, tool


# ---- M2: block_agents ids ----
def test_proposals_and_unread_hide_blocked_docs_from_pat(client, as_ana, ana, admin, db_session):
    sec = mk(client, admin, "NoAgent", 3, block_agents=True)
    d, _ = seed(db_session, ana, title="Hidden")
    label(db_session, d, sec)
    db_session.add(
        Proposal(user_id=ana, kind="label", client="c", payload={"document_id": str(d.id), "label_id": sec})
    )
    db_session.add(
        Notification(
            user_id=ana, kind="upload.searchable", payload={"document_id": str(d.id), "title": "Hidden"}
        )
    )
    db_session.commit()
    tok = pat(db_session, ana, ["read"])
    assert client.get("/me/proposals/count", headers=tok).json() == {"pending": 0}
    got = client.get("/me/proposals", headers=tok).json()
    assert got["total"] == 0 and str(d.id) not in json.dumps(got)
    assert client.get("/me/proposals/count", headers=as_ana).json() == {"pending": 1}
    n = client.get("/me/notifications", headers=tok).json()
    assert n["unread"] == 0
    assert client.get("/me/notifications", headers=as_ana).json()["unread"] == 1


def test_note_notification_of_blocked_note_hidden_from_pat(client, as_ana, ana, admin, db_session):
    sec = mk(client, admin, "NoAgent", 3, block_agents=True)
    d, _ = seed(db_session, ana, title="Plain")
    nid = client.post(f"/documents/{d.id}/annotations",
                      json={"kind": "comment", "body": "n", "visibility": "everyone"}, headers=as_ana).json()["id"]  # fmt: skip
    assert client.put(f"/annotations/{nid}/label", json={"label_id": sec}, headers=as_ana).status_code == 200
    db_session.add(
        Notification(user_id=ana, kind="note.reply", payload={"note_id": nid, "annotation_id": nid})
    )
    db_session.commit()
    tok = pat(db_session, ana, ["read"])
    n = client.get("/me/notifications", headers=tok).json()
    assert n["unread"] == 0 and nid not in json.dumps(n)


def test_export_md_withholds_on_retrieved_and_note_label(client, as_ana, as_bob, ana, bob, admin, db_session):
    sec = mk(client, admin, "NoExp", 3, block_export=True)
    d, v = seed(db_session, ana, title="Plain")
    d.visibility = "everyone"
    db_session.commit()
    nid = client.post(f"/documents/{d.id}/annotations",
                      json={"kind": "comment", "body": "n", "visibility": "everyone"}, headers=as_ana).json()["id"]  # fmt: skip
    assert client.put(f"/annotations/{nid}/label", json={"label_id": sec}, headers=as_ana).status_code == 200
    c = chat_with(db_session, bob, d, v, answer="DERIVED-ANSWER")
    m = db_session.scalars(
        select(ChatMessage).where(ChatMessage.chat_id == c.id, ChatMessage.role == "assistant")
    ).one()
    m.citations = []
    m.retrieved = [{**cite_of(d, v, note_id=nid), "text": "x"}]
    db_session.commit()
    out = client.get(f"/chats/{c.id}/export.md", headers=as_bob).text
    assert "DERIVED-ANSWER" not in out and "[withheld: NoExp]" in out
