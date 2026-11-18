"""Transcript replies built on papers the loader cannot see are withheld (ACL, not label)."""

from datetime import UTC, datetime

import pytest
from api_seed import seed
from conftest import bearer
from test_handoff import mk
from test_labels_mcp import label
from test_labels_mcp import mk as mk_label

from roracle.models import Chat, ChatMessage

TEXT = "paraphrase of SecretTitle"


def _chat(db, ana, doc, *, cited=True, text=TEXT):
    chat = Chat(owner_id=ana, title="C")
    db.add(chat)
    db.flush()
    d, v = doc
    cite = {
        "n": 1,
        "doc_id": str(d.id),
        "version_id": str(v.id),
        "chunk_id": "c1",
        "title": d.title,
        "quote": "q",
    }
    db.add(
        ChatMessage(
            chat_id=chat.id,
            role="assistant",
            content=text,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            citations=[cite] if cited else [],
            retrieved=[] if cited else [cite],
        )
    )
    db.flush()
    return chat


def _items(client, hdr, code):
    return client.get(f"/handoffs/{code}/parts/transcript", headers=hdr).json()["items"]


def _handoff(client, as_ana, chat):
    return mk(client, as_ana, kind="chat", source_id=str(chat.id), audience="authenticated")


def test_reply_from_invisible_paper_is_withheld(client, as_ana, as_bob, db_session, ana, bob):
    D = seed(db_session, ana, title="SecretTitle", visibility="private")
    c = _handoff(client, as_ana, _chat(db_session, ana, D))
    reply = next(m for m in _items(client, as_bob, c["code"]) if m["role"] == "assistant")
    assert reply["content"] == "[withheld: unavailable]"
    assert reply["citations"] == [{"n": 1, "available": False}]
    assert "SecretTitle" not in str(reply)


def test_creator_still_sees_own_reply(client, as_ana, db_session, ana):
    D = seed(db_session, ana, title="SecretTitle", visibility="private")
    c = _handoff(client, as_ana, _chat(db_session, ana, D))
    assert _items(client, as_ana, c["code"])[0]["content"] == TEXT


def test_reply_from_visible_paper_verbatim(client, as_ana, as_bob, db_session, ana, bob):
    D = seed(db_session, ana, title="SecretTitle", visibility="everyone")
    c = _handoff(client, as_ana, _chat(db_session, ana, D))
    assert _items(client, as_bob, c["code"])[0]["content"] == TEXT


def test_retrieved_only_invisible_doc_also_withholds(client, as_ana, as_bob, db_session, ana, bob):
    D = seed(db_session, ana, title="SecretTitle", visibility="private")
    c = _handoff(client, as_ana, _chat(db_session, ana, D, cited=False))
    assert _items(client, as_bob, c["code"])[0]["content"] == "[withheld: unavailable]"


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


def test_invisible_paper_label_name_not_leaked(client, as_ana, as_bob, admin, db_session, ana, bob):
    D = seed(db_session, ana, title="SecretTitle", visibility="private")
    label(db_session, D[0], mk_label(client, admin, "TopSecretLabel", 3, block_agents=True))
    c = _handoff(client, as_ana, _chat(db_session, ana, D))
    r = client.get(f"/handoffs/{c['code']}/parts/transcript", headers=as_bob)
    assert r.json()["items"][0]["content"] == "[withheld: unavailable]"
    assert "TopSecretLabel" not in r.text


def test_reply_mimicking_withheld_prefix_still_unavailable(client, as_ana, as_bob, db_session, ana, bob):
    D = seed(db_session, ana, title="SecretTitle", visibility="private")
    c = _handoff(client, as_ana, _chat(db_session, ana, D, text="[withheld: X] SECRET"))
    r = client.get(f"/handoffs/{c['code']}/parts/transcript", headers=as_bob)
    assert r.json()["items"][0]["content"] == "[withheld: unavailable]"
    assert "SECRET" not in r.text.replace("SecretTitle", "")
