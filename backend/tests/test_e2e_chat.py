# ruff: noqa: F811
"""Real-model e2e: chat SSE (P4), ACL (R19), summary vector + near-dup (P2b), MCP ask (P7).

Asserts structure, never exact LLM wording. Run: ROR_E2E=1 pytest -m e2e -s
"""

import json
import time

import pytest
from e2e_support import E2E_MARKS, FIXTURES, e2e_settings, es, real_llm, upload_and_ingest  # noqa: F401
from fastapi.testclient import TestClient
from mcp_helpers import call
from sqlalchemy import select

from roracle import pats
from roracle.api.routes.library import get_es
from roracle.api.routes.search import get_llm
from roracle.auth import get_app_settings
from roracle.ingest.neardup import cosine
from roracle.models import ChatMessage, DocumentVersion

pytestmark = E2E_MARKS

QUESTION = "What does the quokka heuristic do in this note?"


@pytest.fixture
def wired(app, e2e_settings, es):
    """Point the app at the real ES and model endpoint with the e2e settings."""
    app.dependency_overrides[get_app_settings] = lambda: e2e_settings
    app.dependency_overrides[get_llm] = lambda: real_llm(e2e_settings)
    app.dependency_overrides[get_es] = lambda: es


@pytest.fixture
def ingested(wired, client, as_ana, ana, session_factory, e2e_settings, es):
    data = (FIXTURES / "sample.md").read_bytes()
    doc_id, vid, secs = upload_and_ingest(
        client, as_ana, session_factory, e2e_settings, es, "sample.md", data, "text/markdown"
    )
    return doc_id, vid, secs


def sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def ask(client, headers, question=QUESTION):
    chat = client.post("/chats", headers=headers, json={})
    assert chat.status_code == 201, chat.text
    cid = chat.json()["id"]
    r = client.post(f"/chats/{cid}/messages", headers=headers, json={"content": question})
    assert r.status_code == 200, r.text
    return cid, sse(r.text)


def test_chat_streams_tokens_citations_done(client, as_ana, ingested, db_session, capsys):
    doc_id, _, _ = ingested
    t0 = time.monotonic()
    cid, events = ask(client, as_ana)
    elapsed = time.monotonic() - t0
    names = [e for e, _ in events]
    assert names.count("done") == 1 and names[-1] == "done"
    assert "token" in names
    assert names.index("token") < names.index("citations") < names.index("done")
    cites = next(d for e, d in events if e == "citations")
    items = cites["items"] if isinstance(cites, dict) and "items" in cites else cites
    assert any(c["doc_id"] == doc_id for c in items), items
    msgs = db_session.scalars(
        select(ChatMessage).where(ChatMessage.chat_id == cid).order_by(ChatMessage.created_at)
    ).all()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].status == "complete" and msgs[1].content.strip()
    with capsys.disabled():
        print(f"\n[e2e] chat: {elapsed:.1f}s tokens={names.count('token')} citations={len(items)}")


def test_other_user_gets_no_citation_to_private_doc(client, as_bob, ingested, capsys):
    doc_id, _, _ = ingested
    _, events = ask(client, as_bob)
    assert events[-1][0] == "done"
    for e, d in events:
        assert doc_id not in json.dumps(d), (e, d)  # R19: nothing about ana's doc leaks to bob


def test_summary_vector_and_near_dup(
    client, as_ana, ana, db_session, session_factory, e2e_settings, es, wired, capsys
):
    base = "\n\n".join(
        f"## Section {i}\n\nThe quokka heuristic number {i} reorders candidate passages by lexical "
        f"surprise, measured against a background corpus of laboratory notebooks about marsupial "
        f"ecology, soil chemistry and instrument calibration routine {i * 7}."
        for i in range(1, 9)
    )
    edited = base.replace("calibration routine 7", "calibration routine 8", 1)
    assert edited != base
    _, v1, t1 = upload_and_ingest(
        client, as_ana, session_factory, e2e_settings, es, "a.md", base.encode(), "text/markdown"
    )
    _, v2, t2 = upload_and_ingest(
        client, as_ana, session_factory, e2e_settings, es, "b.md", edited.encode(), "text/markdown"
    )
    db_session.expire_all()
    a = db_session.get(DocumentVersion, v1)
    b = db_session.get(DocumentVersion, v2)
    assert a.summary and a.summary.strip() and a.summary_embedding
    assert b.summary and b.summary_embedding
    cos = cosine(a.summary_embedding, b.summary_embedding)
    with capsys.disabled():
        print(
            f"\n[e2e] near-dup: ingest {t1:.1f}s+{t2:.1f}s summary_cosine={cos:.4f} "
            f"(threshold {e2e_settings.near_dup_summary_threshold}) b.stage={b.stage}"
        )
    assert b.stage == "review", (b.stage, cos)
    assert str(b.near_dup_of) == str(a.id)
    assert a.stage == "searchable"


def test_mcp_ask_returns_answer_with_citations(app, ana, ingested, db_session, capsys):
    doc_id, _, _ = ingested
    _, token = pats.create(db_session, ana, "e2e", ["read"], 30)
    db_session.commit()
    with TestClient(app) as mc:
        t0 = time.monotonic()
        err, out = call(mc, {"Authorization": f"Bearer {token}"}, "ask", question=QUESTION)
        elapsed = time.monotonic() - t0
    assert not err, out
    assert out["answer"].strip()
    assert any(c["doc_id"] == doc_id for c in out["citations"]), out
    with capsys.disabled():
        print(f"\n[e2e] mcp ask: {elapsed:.1f}s citations={len(out['citations'])}")
