import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from api_seed import seed
from conftest import bearer

from roracle import handoff
from roracle.auth import principal_for_user
from roracle.models import AuditLog, Chat, ChatMessage, Collection, CollectionItem, Handoff


def post(client, hdr, **body):
    body.setdefault("kind", "agent")
    return client.post("/handoffs", json=body, headers=hdr)


def mk(client, hdr, **body):
    r = post(client, hdr, **body)
    assert r.status_code == 201, r.text
    return r.json()


def audits(db, action="handoff.load"):
    return db.query(AuditLog).filter(AuditLog.action == action).count()


def test_new_code_alphabet_and_length():
    for _ in range(50):
        c = handoff.new_code()
        assert len(c) == 8 and set(c) <= set(handoff.ALPHABET)
    assert len(handoff.ALPHABET) == 31 and not set("01OIL") & set(handoff.ALPHABET)


@pytest.mark.parametrize(
    "s",
    [
        "7KQ2-XM9P",
        "7kq2xm9p",
        " 7KQ2 XM9P ",
        "roracle://handoff/7KQ2XM9P",
        "https://host/h/7KQ2-XM9P",
        "/h/7kq2-xm9p/",
    ],
)
def test_normalize_forms(s):
    assert handoff.normalize(s) == "7KQ2XM9P"
    assert handoff.display("7KQ2XM9P") == "7KQ2-XM9P"


@pytest.mark.parametrize("s", ["", "7KQ2XM9", "7KQ2XM9PP", "7KQ2XM90", "7KQ2XMIP", "!!!!!!!!"])
def test_normalize_invalid_is_404(s):
    with pytest.raises(Exception) as e:
        handoff.normalize(s)
    assert e.value.status_code == 404


def test_invalid_code_http_404_not_422(client, as_ana, ana):
    assert client.get("/handoffs/short", headers=as_ana).status_code == 404
    assert client.get("/handoffs/ZZZZZZZZ", headers=as_ana).status_code == 404


def test_creator_only_default_and_all_forms_load(client, as_ana, as_bob, ana, bob):
    m = mk(client, as_ana, title="T", brief="do it")
    assert re.fullmatch(r"[A-Z2-9]{8}", m["code"]) and m["url"] == f"/h/{m['display']}"
    assert m["uri"] == f"roracle://handoff/{m['code']}" and m["created_by"] == "ana"
    for form in (m["code"].lower(), m["display"], m["uri"].replace("roracle://handoff/", "")):
        assert client.get(f"/handoffs/{form}", headers=as_ana).json()["brief"] == "do it"
    r = client.get(f"/handoffs/{m['code']}", headers=as_bob)
    assert r.status_code == 404 and r.json() == {"detail": "not found"}
    assert client.get(f"/handoffs/{m['code']}/parts/transcript", headers=as_bob).status_code == 404
    assert client.get("/handoffs", headers=as_bob).json() == []
    assert [h["code"] for h in client.get("/handoffs", headers=as_ana).json()] == [m["code"]]


def test_shared_group_and_user(client, as_ana, as_bob, make_token, ana, bob):
    m = mk(client, as_ana, audience="shared", shares=[{"kind": "group", "principal": "/Physics"}])
    assert client.get(f"/handoffs/{m['code']}", headers=as_bob).status_code == 200
    carl = bearer(make_token(sub="carl", username="carl", groups=("/Chem",)))
    assert client.get(f"/handoffs/{m['code']}", headers=carl).status_code == 404
    m2 = mk(client, as_ana, audience="shared", shares=[{"kind": "user", "principal": str(bob)}])
    assert client.get(f"/handoffs/{m2['code']}", headers=as_bob).status_code == 200
    assert client.get(f"/handoffs/{m2['code']}", headers=carl).status_code == 404


def test_shared_needs_share_and_bad_share(client, as_ana, ana):
    assert post(client, as_ana, audience="shared").status_code == 422
    assert (
        post(client, as_ana, audience="shared", shares=[{"kind": "x", "principal": "a"}]).status_code == 422
    )


def test_authenticated_audience(client, as_ana, as_bob, ana, bob):
    m = mk(client, as_ana, audience="authenticated")
    assert client.get(f"/handoffs/{m['code']}", headers=as_bob).status_code == 200
    assert client.get(f"/handoffs/{m['code']}").status_code == 401


def test_admin_has_no_bypass(client, as_ana, make_token, ana):
    m = mk(client, as_ana)
    admin = bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))
    assert client.get(f"/handoffs/{m['code']}", headers=admin).status_code == 404


def test_revoked_and_expired(client, as_ana, as_bob, make_token, db_session, ana, bob):
    m = mk(client, as_ana, audience="authenticated")
    mine = mk(client, as_ana)
    carl = bearer(make_token(sub="carl", username="carl"))
    sh = mk(client, as_ana, audience="shared", shares=[{"kind": "group", "principal": "/Physics"}])
    assert client.delete(f"/handoffs/{sh['code']}", headers=as_ana).status_code == 204
    assert client.get(f"/handoffs/{sh['code']}", headers=as_bob).status_code == 410
    assert client.get(f"/handoffs/{sh['code']}", headers=carl).status_code == 404  # outsider: no leak
    assert client.delete(f"/handoffs/{sh['code']}", headers=as_ana).status_code == 204  # idempotent
    db_session.query(Handoff).filter(Handoff.code.in_([m["code"], mine["code"]])).update(
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    db_session.flush()
    r = client.get(f"/handoffs/{m['code']}", headers=as_bob)
    assert r.status_code == 410 and r.json()["detail"] == "handoff expired"
    assert client.get(f"/handoffs/{m['code']}/parts/notes", headers=as_bob).status_code == 410
    assert client.post(f"/handoffs/{m['code']}/notes", json={"text": "x"}, headers=as_bob).status_code == 410


def test_expiry_bounds(client, as_ana, ana):
    for d in (0, 91, -1):
        assert post(client, as_ana, expires_in_days=d).status_code == 422
    assert post(client, as_ana, expires_in_days=90).status_code == 201
    assert post(client, as_ana, expires_in_days=1).status_code == 201


def _chat(db, ana, private_doc, public_doc):
    chat = Chat(owner_id=ana, title="Chat T")
    db.add(chat)
    db.flush()
    cite = lambda n, d, v, t: {
        "n": n,
        "doc_id": str(d.id),
        "version_id": str(v.id),
        "chunk_id": f"c{n}",
        "title": t,
        "page": 1,
        "quote": f"quote {t}",
    }
    base = datetime(2026, 1, 1, tzinfo=UTC)
    db.add(ChatMessage(chat_id=chat.id, role="user", content="why?", created_at=base))
    db.add(
        ChatMessage(
            chat_id=chat.id,
            role="assistant",
            content="because [1][2]",
            created_at=base + timedelta(seconds=1),
            citations=[cite(1, *public_doc, "PubTitle"), cite(2, *private_doc, "SecretTitle")],
        )
    )
    db.flush()
    return chat


def test_chat_handoff_redacts_hidden_doc(client, as_ana, as_bob, db_session, ana, bob):
    pub = seed(db_session, ana, title="PubTitle", visibility="everyone")
    priv = seed(db_session, ana, title="SecretTitle", visibility="private")
    chat = _chat(db_session, ana, priv, pub)
    # creator sees everything
    c = mk(client, as_ana, kind="chat", source_id=str(chat.id), audience="authenticated")
    assert c["title"] == "Chat T" and c["withheld"] == 0 and len(c["passages"]) == 2
    assert set(c["doc_ids"]) == {str(pub[0].id), str(priv[0].id)}
    assert c["source"] == {"chat_id": str(chat.id)} and c["parts"] == {"transcript": 1}
    # bob: redacted everywhere
    m = client.get(f"/handoffs/{c['code']}", headers=as_bob).json()
    t = client.get(f"/handoffs/{c['code']}/parts/transcript", headers=as_bob).json()
    blob = json.dumps(m) + json.dumps(t)
    for secret in (str(priv[0].id), str(priv[1].id), "SecretTitle", "quote SecretTitle", "c2"):
        assert secret not in blob, secret
    assert m["withheld"] == 1 and m["doc_ids"] == [str(pub[0].id)]
    assert [x["doc_id"] for x in m["passages"]] == [str(pub[0].id)]
    cites = t["items"][1]["citations"]
    assert cites[0]["available"] is True and cites[1] == {"n": 2, "available": False}
    assert "PubTitle" in blob


def test_creator_losing_access_is_redacted_at_creation(client, as_ana, as_bob, db_session, ana, bob):
    """bob makes a handoff from ana's shared chat; ana's private doc is invisible to bob -> never stored."""
    from roracle.models import ChatShare

    pub = seed(db_session, ana, visibility="everyone")
    priv = seed(db_session, ana, title="SecretTitle", visibility="private")
    chat = _chat(db_session, ana, priv, pub)
    db_session.add(ChatShare(chat_id=chat.id, principal_kind="user", principal=str(bob)))
    db_session.flush()
    c = mk(client, as_bob, kind="chat", source_id=str(chat.id), audience="authenticated")
    h = db_session.get(Handoff, c["code"])
    assert "SecretTitle" not in json.dumps([h.transcript, h.passages, h.doc_ids, c])
    assert str(priv[0].id) not in json.dumps([h.transcript, h.passages, h.doc_ids, c])
    assert c["withheld"] == 0  # unknown to the creator too: nothing counted


def test_chat_not_readable_404(client, as_ana, as_bob, db_session, ana, bob):
    pub = seed(db_session, ana)
    chat = _chat(db_session, ana, pub, pub)
    assert post(client, as_bob, kind="chat", source_id=str(chat.id)).status_code == 404
    assert post(client, as_bob, kind="chat", source_id="nope").status_code == 404


def test_transcript_paging(client, as_ana, db_session, ana):
    chat = Chat(owner_id=ana, title="long")
    db_session.add(chat)
    db_session.flush()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(45):
        db_session.add(
            ChatMessage(chat_id=chat.id, role="user", content=f"m{i}", created_at=base + timedelta(seconds=i))
        )
    db_session.flush()
    c = mk(client, as_ana, kind="chat", source_id=str(chat.id))
    assert c["parts"] == {"transcript": 3}
    p3 = client.get(f"/handoffs/{c['code']}/parts/transcript?page=3", headers=as_ana).json()
    assert [m["content"] for m in p3["items"]] == [f"m{i}" for i in range(40, 45)] and p3["pages"] == 3
    assert client.get(f"/handoffs/{c['code']}/parts/transcript?page=9", headers=as_ana).json()["items"] == []
    assert client.get(f"/handoffs/{c['code']}/parts/bogus", headers=as_ana).status_code == 404
    assert client.get(f"/handoffs/{c['code']}/parts/transcript?page=0", headers=as_ana).status_code == 422


def test_collection_handoff(client, as_ana, as_bob, db_session, ana, bob):
    pub, priv = (
        seed(db_session, ana, title="P"),
        seed(db_session, bob, title="HiddenBob", visibility="private"),
    )
    col = Collection(owner_id=ana, name="Reading")
    db_session.add(col)
    db_session.flush()
    db_session.add_all(
        [
            CollectionItem(collection_id=col.id, document_id=pub[0].id),
            CollectionItem(collection_id=col.id, document_id=priv[0].id),
        ]
    )
    db_session.flush()
    c = mk(client, as_ana, kind="collection", source_id=str(col.id))
    assert c["doc_ids"] == [str(pub[0].id)] and c["title"] == "Reading" and c["source"]["name"] == "Reading"
    assert post(client, as_bob, kind="collection", source_id=str(col.id)).status_code == 404  # owner only


def test_document_search_agent_filter_doc_ids(client, as_ana, as_bob, db_session, ana, bob):
    pub = seed(db_session, ana, visibility="everyone")
    priv = seed(db_session, ana, visibility="private")
    d = mk(client, as_bob, kind="document", source_id=str(pub[0].id))
    assert d["doc_ids"] == [str(pub[0].id)] and d["source"]["doc_id"] == str(pub[0].id)
    assert post(client, as_bob, kind="document", source_id=str(priv[0].id)).status_code == 404
    s = mk(client, as_bob, kind="search", goal="spectral", doc_ids=[str(pub[0].id), str(priv[0].id)])
    assert s["source"] == {"query": "spectral"} and s["doc_ids"] == [str(pub[0].id)]
    a = mk(client, as_bob, kind="agent", brief="b", doc_ids=[str(priv[0].id), "junk"])
    assert a["doc_ids"] == [] and a["withheld"] == 0 and str(priv[0].id) not in json.dumps(a)


def test_loader_losing_doc_sees_withheld(client, as_ana, as_bob, db_session, ana, bob):
    shared = seed(db_session, ana, title="OnlyAna", visibility="private")
    a = mk(client, as_ana, kind="agent", doc_ids=[str(shared[0].id)], audience="authenticated")
    assert a["doc_ids"] == [str(shared[0].id)]
    m = client.get(f"/handoffs/{a['code']}", headers=as_bob).json()
    assert m["doc_ids"] == [] and m["withheld"] == 1 and "OnlyAna" not in json.dumps(m)


def test_every_load_and_part_audited(client, as_ana, db_session, ana):
    m = mk(client, as_ana)
    assert audits(db_session, "handoff.create") == 1 and audits(db_session) == 0
    client.get(f"/handoffs/{m['code']}", headers=as_ana)
    assert audits(db_session) == 1
    client.get(f"/handoffs/{m['code']}/parts/transcript", headers=as_ana)
    client.get(f"/handoffs/{m['code']}/parts/notes", headers=as_ana)
    assert audits(db_session) == 3
    row = db_session.query(AuditLog).filter(AuditLog.detail["part"].astext == "notes").one()
    assert row.target == m["code"] and row.user_id == ana


def test_notes(client, as_ana, as_bob, db_session, ana, bob):
    m = mk(client, as_ana, audience="authenticated")
    r = client.post(f"/handoffs/{m['code']}/notes", json={"text": "found it"}, headers=as_bob)
    assert r.status_code == 201 and r.json()["by"] == "bob" and r.json()["via"] == "ui"
    client.post(f"/handoffs/{m['code']}/notes", json={"text": "second"}, headers=as_ana)
    got = client.get(f"/handoffs/{m['code']}", headers=as_ana).json()["notes"]
    assert [n["text"] for n in got] == ["found it", "second"]
    items = client.get(f"/handoffs/{m['code']}/parts/notes", headers=as_ana).json()["items"]
    assert len(items) == 2
    for bad in ("", "x" * 10001):
        assert (
            client.post(f"/handoffs/{m['code']}/notes", json={"text": bad}, headers=as_ana).status_code == 422
        )
    assert (
        client.post(f"/handoffs/{m['code']}/notes", json={"text": "x"}, headers=bearer("junk")).status_code
        == 401
    )


def test_note_requires_write_scope_and_marks_mcp(client, as_ana, db_session, ana):
    m = mk(client, as_ana)
    p = principal_for_user(db_session, ana)
    with pytest.raises(Exception) as e:
        handoff.append_note(db_session, replace(p, scopes=frozenset({"read"})), m["code"], "x")
    assert e.value.status_code == 403
    n = handoff.append_note(db_session, replace(p, client="pat:cli"), m["code"], "y", via="ui")
    assert n["via"] == "mcp"
    # and read-only PAT over REST is blocked by current_principal
    tok = client.post("/me/tokens", json={"name": "r", "scopes": ["read"]}, headers=as_ana).json()["token"]
    r = client.post(f"/handoffs/{m['code']}/notes", json={"text": "x"}, headers=bearer(tok))
    assert r.status_code == 403
    assert client.get(f"/handoffs/{m['code']}", headers=bearer(tok)).status_code == 200


def test_patch_and_revoke_creator_only(client, as_ana, as_bob, db_session, ana, bob):
    m = mk(client, as_ana, audience="authenticated")
    c = m["code"]
    assert client.patch(f"/handoffs/{c}", json={"title": "x"}, headers=as_bob).status_code == 403
    assert client.delete(f"/handoffs/{c}", headers=as_bob).status_code == 403
    out = client.patch(
        f"/handoffs/{c}",
        json={"title": "New", "goal": "g", "brief": "b", "expires_in_days": 30},
        headers=as_ana,
    ).json()
    assert (out["title"], out["goal"], out["brief"]) == ("New", "g", "b")
    exp = datetime.fromisoformat(out["expires_at"])
    assert timedelta(days=29) < exp - datetime.now(UTC) <= timedelta(days=30)
    assert client.patch(f"/handoffs/{c}", json={"audience": "shared"}, headers=as_ana).status_code == 422
    r = client.patch(
        f"/handoffs/{c}",
        json={"audience": "shared", "shares": [{"kind": "user", "principal": str(bob)}]},
        headers=as_ana,
    )
    assert r.status_code == 200 and r.json()["audience"] == "shared"
    assert client.get(f"/handoffs/{c}", headers=as_bob).status_code == 200
    client.patch(
        f"/handoffs/{c}", json={"shares": [{"kind": "group", "principal": "/Other"}]}, headers=as_ana
    )
    assert client.get(f"/handoffs/{c}", headers=as_bob).status_code == 404
    assert client.patch(f"/handoffs/{c}", json={"expires_in_days": 91}, headers=as_ana).status_code == 422
    # outsider PATCH/DELETE -> 404
    private = mk(client, as_ana)
    assert (
        client.patch(f"/handoffs/{private['code']}", json={"title": "x"}, headers=as_bob).status_code == 404
    )
    assert client.delete(f"/handoffs/{private['code']}", headers=as_bob).status_code == 404
    client.delete(f"/handoffs/{c}", headers=as_ana)
    assert client.patch(f"/handoffs/{c}", json={"title": "x"}, headers=as_ana).status_code == 410
    assert audits(db_session, "handoff.revoke") == 1


def test_code_collision_retry(client, as_ana, monkeypatch, ana):
    first = mk(client, as_ana)["code"]
    seq = iter([first, first, "AAAAAAAA"])
    monkeypatch.setattr(handoff, "new_code", lambda: next(seq))
    assert mk(client, as_ana)["code"] == "AAAAAAAA"
    monkeypatch.setattr(handoff, "new_code", lambda: first)
    assert post(client, as_ana).status_code == 500
    assert client.get(f"/handoffs/{first}", headers=as_ana).status_code == 200  # session still healthy


def test_list_mine_newest_first(client, as_ana, ana):
    a, b = mk(client, as_ana, title="a"), mk(client, as_ana, title="b")
    rows = client.get("/handoffs", headers=as_ana).json()
    assert [r["code"] for r in rows] == [b["code"], a["code"]]
    assert set(rows[0]) == {
        "code",
        "display",
        "kind",
        "title",
        "audience",
        "expires_at",
        "revoked_at",
        "created_at",
        "created_via",
    }


def test_pat_created_handoff_is_mcp(client, as_ana, ana):
    tok = client.post("/me/tokens", json={"name": "w", "scopes": ["write"]}, headers=as_ana).json()["token"]
    assert mk(client, bearer(tok))["created_via"] == "mcp"
    assert mk(client, as_ana)["created_via"] == "ui"
