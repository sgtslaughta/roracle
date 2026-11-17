import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api_seed import seed

from roracle.auth import ADMIN_ROLE
from roracle.models import AnswerRating, Chat, ChatMessage, ChatShare


def cite(n, d, v, **kw):
    return {"n": n, "doc_id": str(d.id), "version_id": str(v.id), "version": 1, "chunk_id": "c", "title": d.title,
            "page": 2, "bbox": None, "quote": "q", "authors": ["Ann"], "year": 2020, **kw}  # fmt: skip


def retr(n, d, v):
    return {"n": n, "doc_id": str(d.id), "version_id": str(v.id), "title": d.title, "score": 1.0, "text": "t"}


@pytest.fixture
def world(db_session, client, as_ana, as_bob, ana, bob):
    """ana owns a chat citing a visible (everyone) and a private doc; bob is a stranger."""
    pub, pv = seed(db_session, ana, title="Public", visibility="everyone")
    priv, prv = seed(db_session, ana, title="Secret", visibility="private")
    chat = Chat(owner_id=ana, title="Hello")
    db_session.add(chat)
    db_session.flush()
    q = ChatMessage(
        chat_id=chat.id,
        role="user",
        content="why?",
        viewer={"tabs": []},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    a = ChatMessage(
        chat_id=chat.id, role="assistant", content="Because [1][2].", model="m", created_at=datetime(2026, 1, 2, tzinfo=UTC),
        citations=[cite(1, pub, pv), cite(2, priv, prv)], retrieved=[retr(1, pub, pv), retr(2, priv, prv)],
    )  # fmt: skip
    db_session.add_all([q, a])
    db_session.flush()
    return {"chat": chat, "q": q, "a": a}


def share_user(db, chat, uid):
    db.add(ChatShare(chat_id=chat.id, principal_kind="user", principal=str(uid)))
    db.flush()


def test_models(client, as_ana):
    r = client.get("/chats/models", headers=as_ana)
    assert r.status_code == 200
    assert r.json() == {"models": ["qwen2.5:14b"], "default": "qwen2.5:14b"}


def test_owner_sees_all_citations(client, as_ana, world):
    r = client.get(f"/chats/{world['chat'].id}", headers=as_ana).json()
    assert r["is_owner"] is True
    q, a = r["messages"]
    assert q["role"] == "user" and q["viewer"] == {"tabs": []} and q["rating"] is None
    assert [c["available"] for c in a["citations"]] == [True, True]
    assert a["content"] == "Because [1][2]."
    assert len(a["retrieved"]) == 2 and a["status"] == "complete" and a["model"] == "m"


def test_shared_user_rechecked(client, db_session, as_bob, bob, world):
    share_user(db_session, world["chat"], bob)
    r = client.get(f"/chats/{world['chat'].id}", headers=as_bob).json()
    assert r["is_owner"] is False
    q, a = r["messages"]
    assert q["viewer"] is None and a["content"] == "Because [1][2]."
    assert a["citations"][0]["available"] is True and a["citations"][0]["title"] == "Public"
    assert a["citations"][1] == {"n": 2, "available": False}
    assert [x["n"] for x in a["retrieved"]] == [1]


def test_shared_group(client, db_session, make_token, world):
    from conftest import bearer

    h = bearer(make_token(sub="eve", username="eve", groups=("/Team",)))
    assert client.get(f"/chats/{world['chat'].id}", headers=h).status_code == 404
    db_session.add(ChatShare(chat_id=world["chat"].id, principal_kind="group", principal="/Team"))
    db_session.flush()
    r = client.get(f"/chats/{world['chat'].id}", headers=h)
    assert r.status_code == 200 and r.json()["messages"][1]["citations"][1] == {"n": 2, "available": False}


def test_stranger_404(client, as_bob, bob, world):
    cid = world["chat"].id
    assert client.get(f"/chats/{cid}", headers=as_bob).json() == {"detail": "not found"}
    assert client.get(f"/chats/{cid}/export.md", headers=as_bob).status_code == 404
    assert client.get(f"/chats/{cid}/shares", headers=as_bob).status_code == 404
    r = client.put(f"/messages/{world['a'].id}/rating", headers=as_bob, json={"value": 1})
    assert r.status_code == 404


def test_admin_no_implicit_access(client, make_token, world):
    from conftest import bearer

    h = bearer(make_token(sub="root", username="root", roles=(ADMIN_ROLE,)))
    assert client.get(f"/chats/{world['chat'].id}", headers=h).status_code == 404


def test_reader_is_read_only(client, db_session, as_bob, bob, world):
    share_user(db_session, world["chat"], bob)
    cid = world["chat"].id
    assert client.patch(f"/chats/{cid}", headers=as_bob, json={"title": "x"}).status_code == 403
    r = client.put(f"/chats/{cid}/shares", headers=as_bob, json={"users": [], "groups": []})
    assert r.status_code == 403 and r.json() == {"detail": "read-only"}
    assert client.get(f"/chats/{cid}/shares", headers=as_bob).status_code == 403
    r = client.post(f"/chats/{cid}/messages", headers=as_bob, json={"content": "hi"})
    assert r.status_code == 403


def test_export(client, db_session, as_bob, bob, as_ana, world):
    cid = world["chat"].id
    r = client.get(f"/chats/{cid}/export.md", headers=as_ana)
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"] == f'attachment; filename="chat-{cid}.md"'
    assert r.text.startswith("# Hello")
    assert "**You:** why?" in r.text and "**rOracle:** Because [1][2]." in r.text
    assert "[1] Ann, Public, 2020, p. 2" in r.text and "[2] Ann, Secret, 2020, p. 2" in r.text
    share_user(db_session, world["chat"], bob)
    t = client.get(f"/chats/{cid}/export.md", headers=as_bob).text
    assert "[1] Ann, Public, 2020, p. 2" in t and "[2] Source not available" in t
    assert "Secret" not in t


def test_rating_flow(client, db_session, as_ana, as_bob, bob, world):
    share_user(db_session, world["chat"], bob)
    mid, cid = world["a"].id, world["chat"].id
    r = client.put(f"/messages/{mid}/rating", headers=as_bob, json={"value": -1, "comment": "meh"})
    assert r.json() == {"value": -1, "comment": "meh"}
    client.put(f"/messages/{mid}/rating", headers=as_bob, json={"value": 1})
    msgs = client.get(f"/chats/{cid}", headers=as_bob).json()["messages"]
    assert msgs[1]["rating"] == {"value": 1, "comment": None} and msgs[0]["rating"] is None
    assert client.get(f"/chats/{cid}", headers=as_ana).json()["messages"][1]["rating"] is None
    r = client.put(f"/messages/{mid}/rating", headers=as_bob, json={"value": 0})
    assert r.json() == {"value": 0, "comment": None}
    assert db_session.get(AnswerRating, (mid, bob)) is None
    assert client.get(f"/chats/{cid}", headers=as_bob).json()["messages"][1]["rating"] is None


def test_rating_validation(client, as_ana, world):
    h = {"headers": as_ana}
    assert client.put(f"/messages/{world['q'].id}/rating", json={"value": 1}, **h).status_code == 422
    assert client.put(f"/messages/{world['a'].id}/rating", json={"value": 2}, **h).status_code == 422
    r = client.put(f"/messages/{world['a'].id}/rating", json={"value": 1, "comment": "x" * 2001}, **h)
    assert r.status_code == 422
    assert client.put(f"/messages/{uuid.uuid4()}/rating", json={"value": 1}, **h).status_code == 404


def test_admin_ratings(client, db_session, as_ana, as_bob, bob, make_token, world):
    from conftest import bearer

    share_user(db_session, world["chat"], bob)
    client.put(f"/messages/{world['a'].id}/rating", headers=as_bob, json={"value": -1, "comment": "bad"})
    r = client.get("/admin/ratings", headers=as_ana)
    assert r.status_code == 403 and r.json() == {"detail": "admin only"}
    h = bearer(make_token(sub="root", username="root", roles=(ADMIN_ROLE,)))
    rows = client.get("/admin/ratings", headers=h).json()
    assert len(rows) == 1
    assert rows[0] | {"created_at": None} == {
        "message_id": str(world["a"].id), "chat_id": str(world["chat"].id), "user_id": str(bob),
        "username": "bob", "value": -1, "comment": "bad", "created_at": None, "model": "m",
        "answer": "Because [1][2].",
    }  # fmt: skip
    assert rows[0]["created_at"]


def test_list_paging(client, db_session, as_ana, as_bob, ana, bob):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(3):
        db_session.add(Chat(owner_id=ana, title=f"c{i}", updated_at=base + timedelta(hours=i)))
    db_session.add(Chat(owner_id=bob, title="bobs", updated_at=base))
    db_session.flush()
    r = client.get("/chats", headers=as_ana, params={"limit": 2}).json()
    assert [c["title"] for c in r["items"]] == ["c2", "c1"] and r["next"]
    r2 = client.get("/chats", headers=as_ana, params={"limit": 2, "before": r["next"]}).json()
    assert [c["title"] for c in r2["items"]] == ["c0"] and r2["next"] is None
    assert client.get("/chats", headers=as_ana, params={"limit": 101}).status_code == 422


def test_patch(client, as_ana, world):
    cid = world["chat"].id
    pin = str(uuid.uuid4())
    r = client.patch(
        f"/chats/{cid}", headers=as_ana, json={"title": "New", "pins": [pin.upper()], "scope": "open"}
    )
    j = r.json()
    assert r.status_code == 200 and j["title"] == "New" and j["pins"] == [pin] and j["scope"] == "open"
    assert client.patch(f"/chats/{cid}", headers=as_ana, json={"title": "x" * 201}).status_code == 422
    assert client.patch(f"/chats/{cid}", headers=as_ana, json={"pins": ["nope"]}).status_code == 422
    assert client.patch(f"/chats/{cid}", headers=as_ana, json={"model": "gpt-x"}).status_code == 422
    assert (
        client.patch(f"/chats/{cid}", headers=as_ana, json={"scope": "collection:" + pin}).status_code == 422
    )
    assert client.patch(f"/chats/{cid}", headers=as_ana, json={"excludes": [pin]}).json()["excludes"] == [pin]


def test_shares_put_get(client, db_session, as_ana, ana, bob, world):
    cid = world["chat"].id
    r = client.put(
        f"/chats/{cid}/shares", headers=as_ana,
        json={"users": [str(bob), str(ana), str(bob).upper()], "groups": ["/Team", "/A/B", "/Team"]},
    )  # fmt: skip
    assert r.status_code == 200
    assert r.json() == {"users": [str(bob)], "groups": ["/A/B", "/Team"]}
    for bad in ("Team", ""):
        r = client.put(f"/chats/{cid}/shares", headers=as_ana, json={"users": [], "groups": [bad]})
        assert r.status_code == 422
    assert client.get(f"/chats/{cid}/shares", headers=as_ana).json() == {
        "users": [str(bob)],
        "groups": ["/A/B", "/Team"],
    }
    r = client.put(f"/chats/{cid}/shares", headers=as_ana, json={"users": [], "groups": []})
    assert r.json() == {"users": [], "groups": []}


def test_shares_unknown_user(client, as_ana, world):
    for bad in (str(uuid.uuid4()), "nope"):
        r = client.put(
            f"/chats/{world['chat'].id}/shares", headers=as_ana, json={"users": [bad], "groups": []}
        )
        assert r.status_code == 422 and r.json() == {"detail": "unknown user"}


def test_reader_hides_owner_config(client, db_session, as_ana, as_bob, bob, world):
    chat = world["chat"]
    cid = str(uuid.uuid4())
    chat.pins, chat.excludes, chat.scope = [cid], [cid], f"collection:{cid}"
    share_user(db_session, chat, bob)
    j = client.get(f"/chats/{chat.id}", headers=as_bob).json()
    assert (j["pins"], j["excludes"], j["scope"]) == ([], [], "collection")
    j = client.get(f"/chats/{chat.id}", headers=as_ana).json()
    assert (j["pins"], j["scope"]) == ([cid], f"collection:{cid}")


def test_cursor_ties_and_invalid(client, db_session, as_ana, ana):
    t = datetime(2026, 1, 1, tzinfo=UTC)
    ids = {Chat(owner_id=ana, title=f"t{i}", updated_at=t) for i in range(3)}
    db_session.add_all(ids)
    db_session.flush()
    seen, cur = [], None
    for _ in range(3):
        r = client.get(
            "/chats", headers=as_ana, params={"limit": 1, **({"before": cur} if cur else {})}
        ).json()
        seen += [c["id"] for c in r["items"]]
        cur = r["next"]
    assert sorted(seen) == sorted(str(c.id) for c in ids) and len(set(seen)) == 3
    assert client.get("/chats", headers=as_ana, params={"before": "garbage"}).status_code == 422
    assert client.get("/chats", headers=as_ana, params={"before": "2026-01-01"}).status_code == 422


def test_rating_bumps_created_at(client, db_session, as_ana, ana, world):
    mid = world["a"].id
    client.put(f"/messages/{mid}/rating", headers=as_ana, json={"value": 1})
    row = db_session.get(AnswerRating, (mid, ana))
    row.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    db_session.flush()
    client.put(f"/messages/{mid}/rating", headers=as_ana, json={"value": -1})
    db_session.refresh(row)
    assert row.created_at.year >= 2026


def test_rejected_version_unavailable(client, db_session, as_ana, ana, world):
    _, v = seed(db_session, ana, title="Gone", stage="rejected")
    d = db_session.get(type(v).__mapper__.class_, v.id).document_id
    world["a"].citations = [{**cite(1, world["chat"], v), "doc_id": str(d)}]
    db_session.flush()
    a = client.get(f"/chats/{world['chat'].id}", headers=as_ana).json()["messages"][1]
    assert a["citations"] == [{"n": 1, "available": False}]


def test_hide_chat(client, db_session, as_ana, as_bob, bob, world):
    cid = world["chat"].id
    share_user(db_session, world["chat"], bob)
    assert client.delete(f"/chats/{cid}", headers=as_bob).status_code == 403
    assert client.delete(f"/chats/{uuid.uuid4()}", headers=as_ana).status_code == 404
    assert [c["id"] for c in client.get("/chats", headers=as_ana).json()["items"]] == [str(cid)]
    db_session.commit()
    before = world["chat"].updated_at
    assert client.delete(f"/chats/{cid}", headers=as_ana).status_code == 204
    db_session.refresh(world["chat"])
    assert world["chat"].updated_at == before and world["chat"].hidden_at is not None
    assert client.delete(f"/chats/{cid}", headers=as_ana).status_code == 204  # idempotent
    assert client.get("/chats", headers=as_ana).json()["items"] == []
    assert client.get(f"/chats/{cid}", headers=as_ana).status_code == 200
    assert client.get(f"/chats/{cid}", headers=as_bob).status_code == 200  # shares untouched
    assert len(client.get(f"/chats/{cid}", headers=as_ana).json()["messages"]) == 2


def test_hide_stranger_404(client, as_bob, world):
    assert client.delete(f"/chats/{world['chat'].id}", headers=as_bob).status_code == 404
