# ruff: noqa: F811
"""Fix round: derived chat content, agent text withholding (exclude_llm), handoff transcripts, minors."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from api_seed import seed
from conftest import bearer
from mcp_helpers import call
from sqlalchemy import select
from test_documents_read import put
from test_mcp_tools import fes, llm, mc, pat  # noqa: F401
from test_search_api import hit

from roracle.models import Chat, ChatMessage, Notification

T0 = datetime(2026, 1, 1, tzinfo=UTC)
pytestmark = pytest.mark.usefixtures("_blob_store_override")


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


def mk(client, admin, name, rank, **gates):
    r = client.post(
        "/admin/labels",
        json={"name": name, "colour": "#aabbcc", "rank": rank, "gates": gates},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def label(db, d, lid):
    d.label_id = lid
    db.commit()


def cite_of(d, v, **kw):
    return {"n": 1, "doc_id": str(d.id), "version_id": str(v.id), "title": d.title, "page": 1,
            "quote": "SECRET-QUOTE", "available": True, **kw}  # fmt: skip


def chat_with(db, owner, d, v, answer="SECRET-ANSWER [1]"):
    c = Chat(owner_id=owner, title="t", scope="all")
    db.add(c)
    db.flush()
    db.add(ChatMessage(chat_id=c.id, role="user", content="q", status="complete", created_at=T0))
    db.add(
        ChatMessage(
            chat_id=c.id, role="assistant", content=answer, citations=[cite_of(d, v)],
            retrieved=[{**cite_of(d, v), "text": "SECRET-TEXT"}], status="complete",
            created_at=T0 + timedelta(seconds=1),
        )
    )  # fmt: skip
    db.commit()
    return c


@pytest.mark.parametrize("gate", ["block_agents", "exclude_llm"])
def test_pat_chat_read_withholds_derived_content(client, as_ana, ana, admin, db_session, gate):
    sec = mk(client, admin, "Sec", 3, **{gate: True})
    d, v = seed(db_session, ana, title="Locked")
    label(db_session, d, sec)
    c = chat_with(db_session, ana, d, v)
    tok = pat(db_session, ana, ["read"])
    body = client.get(f"/chats/{c.id}", headers=tok).json()
    assert [m["content"] for m in body["messages"]] == ["q", "[withheld: Sec]"]
    assert "SECRET" not in json.dumps(body)
    human = client.get(f"/chats/{c.id}", headers=as_ana).json()
    assert human["messages"][1]["content"] == "SECRET-ANSWER [1]"


def test_pat_chat_read_plain_doc_untouched(client, ana, db_session):
    d, v = seed(db_session, ana, title="Plain")
    c = chat_with(db_session, ana, d, v)
    tok = pat(db_session, ana, ["read"])
    assert client.get(f"/chats/{c.id}", headers=tok).json()["messages"][1]["content"] == "SECRET-ANSWER [1]"


def test_replay_history_drops_turns_citing_now_excluded_docs(client, admin, ana, db_session):
    from roracle.auth import Principal
    from roracle.labels.textgate import replay_history

    ba = mk(client, admin, "NoAgent", 3, block_agents=True)
    ex = mk(client, admin, "NoLLM", 4, exclude_llm=True, block_agents=True)
    d1, v1 = seed(db_session, ana, title="A")
    d2, v2 = seed(db_session, ana, title="B")
    keep, v3 = seed(db_session, ana, title="C")
    c = Chat(owner_id=ana, title="t", scope="all")
    db_session.add(c)
    db_session.flush()
    for i, (d, v) in enumerate(((d1, v1), (d2, v2), (keep, v3))):
        t = T0 + timedelta(minutes=i)
        db_session.add(
            ChatMessage(chat_id=c.id, role="user", content=f"q{d.title}", status="complete", created_at=t)
        )
        db_session.add(
            ChatMessage(chat_id=c.id, role="assistant", content=f"a{d.title}", citations=[cite_of(d, v)],
                        status="complete", created_at=t + timedelta(seconds=1))
        )  # fmt: skip
        db_session.flush()
    label(db_session, d1, ex)
    label(db_session, d2, ba)
    hist = list(db_session.scalars(select(ChatMessage).where(ChatMessage.chat_id == c.id).order_by(
        ChatMessage.created_at, ChatMessage.id)))  # fmt: skip

    def P(via):
        return Principal(user_id=ana, sub="ana", username="ana", groups=(), is_admin=False, via=via)

    human = [m.content for m in replay_history(db_session, P("jwt"), hist)]
    assert human == ["qB", "aB", "qC", "aC"]  # only exclude_llm binds humans
    agent = [m.content for m in replay_history(db_session, P("pat"), hist)]
    assert agent == ["qC", "aC"]


def make_handoff(client, headers, chat_id):
    r = client.post(
        "/handoffs", json={"kind": "chat", "source_id": str(chat_id), "audience": "authenticated"},
        headers=headers,
    )  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()["code"]


@pytest.mark.parametrize("gate", ["block_agents", "exclude_llm"])
def test_handoff_transcript_withheld_per_loader(client, as_ana, as_bob, ana, bob, admin, db_session, gate):
    sec = mk(client, admin, "Sec", 3, **{gate: True})
    d, v = seed(db_session, ana, title="Locked")
    c = chat_with(db_session, ana, d, v)
    label(db_session, d, sec)  # raised AFTER the chat; the handoff is created by the human
    code = make_handoff(client, as_ana, c.id)
    url = f"/handoffs/{code}/parts/transcript"
    agent = client.get(url, headers=pat(db_session, bob, ["read"])).json()
    assert [m["content"] for m in agent["items"]] == ["q", "[withheld: Sec]"]
    assert "SECRET" not in json.dumps(agent) and "doc_ids" not in json.dumps(agent)
    human = client.get(url, headers=as_bob).json()
    # R10: block_agents sources are withheld from every loader; exclude_llm text only from agents
    assert human["items"][1]["content"] == (
        "[withheld: Sec]" if gate == "block_agents" else "SECRET-ANSWER [1]"
    )
    assert "doc_ids" not in json.dumps(human)
    man = client.get(f"/handoffs/{code}", headers=pat(db_session, bob, ["read"])).json()
    assert "SECRET" not in json.dumps(man)


# ---- exclude_llm: agents get metadata, no text ----
@pytest.fixture
def locked(client, ana, admin, db_session, store):
    sec = mk(client, admin, "NoLLM", 3, exclude_llm=True)
    d, v = seed(db_session, ana, title="Locked")
    put(store, v, b"%PDF-bytes")
    label(db_session, d, sec)
    return d, v


def test_pat_rest_search_and_detail_hide_text(client, as_ana, ana, db_session, fes, locked):
    d, v = locked
    fes.hits = [hit(d, v, "SECRET-TEXT")]
    tok = pat(db_session, ana, ["read"])
    h = client.get("/search", params={"q": "alpha"}, headers=tok).json()["results"][0]
    assert h["title"] == "Locked" and h["passages"] == [] and h["withheld"] == "NoLLM"
    assert client.get("/search", params={"q": "alpha"}, headers=as_ana).json()["results"][0]["passages"]
    det = client.get(f"/documents/{d.id}", headers=tok).json()
    assert det["summary"] is None and det["title"] == "Locked"
    assert client.get(f"/documents/{d.id}", headers=as_ana).json()["summary"] == "sum"


def test_pat_raw_bytes_403_human_ok(client, as_ana, ana, db_session, locked):
    d, _ = locked
    tok = pat(db_session, ana, ["read"])
    for sub in ("file", "pdf", "docling"):
        assert client.get(f"/documents/{d.id}/versions/1/{sub}", headers=tok).status_code == 403
    assert client.get(f"/documents/{d.id}/versions/1/file", headers=as_ana).status_code == 200


def test_mcp_exclude_llm_withholds_text(mc, ana, db_session, fes, locked):
    d, v = locked
    fes.hits = [hit(d, v, "SECRET-TEXT")]
    h = pat(db_session, ana, ["read"])
    err, out = call(mc, h, "search", query="alpha")
    hit0 = out["results"][0]
    assert not err and hit0["title"] == "Locked" and hit0["passages"] == [] and hit0["withheld"] == "NoLLM"
    err, out = call(mc, h, "get_passages", chunk_ids=[f"{v.id}:0"])
    assert not err and [p["text"] for p in out["result"]] == ["[withheld: NoLLM]"]
    err, out = call(mc, h, "get_document", doc_id=str(d.id))
    assert not err and out["summary"] is None and out["title"] == "Locked"


def test_pat_note_body_withheld_existence_kept(client, as_ana, ana, db_session, locked):
    d, _ = locked
    r = client.post(
        f"/documents/{d.id}/annotations",
        json={"kind": "comment", "body": "SECRET-NOTE", "visibility": "everyone"},
        headers=as_ana,
    )
    assert r.status_code == 201, r.text
    tok = pat(db_session, ana, ["read"])
    got = client.get(f"/documents/{d.id}/annotations", headers=tok).json()
    assert got["total"] == 1 and got["items"][0]["body"] == "[withheld: NoLLM]"
    assert "SECRET-NOTE" not in json.dumps(got)
    assert (
        client.get(f"/documents/{d.id}/annotations", headers=as_ana).json()["items"][0]["body"]
        == "SECRET-NOTE"
    )


# ---- minors ----
def test_notifications_hide_agent_blocked_docs_for_pat(client, as_ana, ana, admin, db_session):
    sec = mk(client, admin, "NoAgent", 3, block_agents=True)
    d, _ = seed(db_session, ana, title="Hidden")
    ok, _ = seed(db_session, ana, title="Plain")
    label(db_session, d, sec)
    for doc in (d, ok):
        db_session.add(Notification(user_id=ana, kind="upload.searchable",
                                    payload={"document_id": str(doc.id), "title": doc.title}))  # fmt: skip
    db_session.commit()
    tok = pat(db_session, ana, ["read"])
    items = client.get("/me/notifications", headers=tok).json()["items"]
    assert sorted(json.dumps(i["payload"]) for i in items if "withheld" in i["payload"]) == [
        '{"withheld": true}'
    ]
    assert "Hidden" not in json.dumps(items) and "Plain" in json.dumps(items)
    assert "Hidden" in json.dumps(client.get("/me/notifications", headers=as_ana).json())


def test_open_scope_only_excluded_tabs_is_canned_without_llm(client, as_ana, ana, db_session, locked):
    from test_chat_stream import FakeStreamLLM, new_chat, sse

    d, _ = locked
    llm = FakeStreamLLM()
    client.app.dependency_overrides[__import__("roracle.api.routes.search", fromlist=["x"]).get_llm] = (
        lambda: llm
    )
    cid = new_chat(client, as_ana, scope="open")
    r = client.post(
        f"/chats/{cid}/messages",
        headers=as_ana,
        json={"content": "q", "viewer": {"tabs": [{"doc_id": str(d.id)}]}},
    )
    assert r.status_code == 200
    ev = sse(r)
    assert ev[0] == ("meta", {"excluded_by_label": [{"doc_id": str(d.id), "label": "NoLLM"}]})
    assert llm.received == [] and "None of the open documents" in r.text


def test_chat_export_withholds_answer_citing_export_blocked(
    client, as_ana, as_bob, ana, bob, admin, db_session
):
    sec = mk(client, admin, "NoExport", 3, block_export=True)
    d, v = seed(db_session, ana, title="Locked")
    label(db_session, d, sec)
    c = chat_with(db_session, bob, d, v)
    md = client.get(f"/chats/{c.id}/export.md", headers=as_bob).text
    assert "[withheld: NoExport]" in md and "SECRET-ANSWER" not in md
    # the owner of the doc exports freely
    c2 = chat_with(db_session, ana, d, v)
    assert "SECRET-ANSWER" in client.get(f"/chats/{c2.id}/export.md", headers=as_ana).text


def test_community_notes_strict_one_llm_allowed_query(db_session, ana):
    from sqlalchemy import event

    from roracle.auth import Principal
    from roracle.notes.community import community_notes

    docs = [seed(db_session, ana, title=f"D{i}")[0] for i in range(5)]
    db_session.commit()
    sql: list[str] = []
    event.listen(db_session.get_bind(), "before_cursor_execute", lambda *a: sql.append(a[2]))
    p = Principal(user_id=ana, sub="ana", username="ana", groups=(), is_admin=False)
    community_notes(db_session, p, {str(d.id): {1} for d in docs}, strict=(1, 0.5))
    assert len([s for s in sql if s.startswith("SELECT documents.id")]) == 1
