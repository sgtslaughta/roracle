import json

import pytest
from api_seed import seed
from conftest import bearer
from test_handoff import _chat, mk, post

from roracle import handoff
from roracle.auth import principal_for_user
from roracle.models import Collection, Handoff


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
        "url",
        "kind",
        "title",
        "audience",
        "expires_at",
        "revoked_at",
        "created_at",
        "created_via",
    }


def test_rest_created_via_is_ui(client, as_ana, ana):
    tok = client.post("/me/tokens", json={"name": "w", "scopes": ["write"]}, headers=as_ana).json()["token"]
    assert mk(client, bearer(tok))["created_via"] == "ui"  # REST is always "ui"; MCP passes via explicitly


def test_source_redacted_for_loader(client, as_ana, as_bob, db_session, ana, bob):
    pub = seed(db_session, ana, title="PubTitle", visibility="everyone")
    priv = seed(db_session, ana, title="SecretTitle", visibility="private")
    chat = _chat(db_session, ana, priv, pub)
    col = Collection(owner_id=ana, name="Hidden-Reading-List")
    db_session.add(col)
    db_session.flush()
    d_priv = mk(client, as_ana, kind="document", source_id=str(priv[0].id), audience="authenticated")
    d_pub = mk(client, as_ana, kind="document", source_id=str(pub[0].id), audience="authenticated")
    c = mk(client, as_ana, kind="chat", source_id=str(chat.id), audience="authenticated")
    k = mk(client, as_ana, kind="collection", source_id=str(col.id), title="T", audience="authenticated")
    assert d_priv["source"]["doc_id"] == str(priv[0].id) and c["source"] and k["source"]["name"]
    get = lambda h: client.get(f"/handoffs/{h['code']}", headers=as_bob).json()
    m = get(d_priv)
    assert m["source"] == {} and str(priv[0].id) not in json.dumps(m) and m["withheld"] == 1
    assert get(d_pub)["source"]["doc_id"] == str(pub[0].id)
    assert get(c)["source"] == {} and str(chat.id) not in json.dumps(get(c))
    mk_ = get(k)
    assert (
        mk_["source"] == {}
        and str(col.id) not in json.dumps(mk_)
        and "Hidden-Reading-List" not in json.dumps(mk_)
    )
    assert (
        client.get(f"/handoffs/{k['code']}", headers=as_ana).json()["source"]["name"] == "Hidden-Reading-List"
    )


def test_username_share_never_matches(client, as_ana, as_bob, ana, bob):
    m = mk(client, as_ana, audience="shared", shares=[{"kind": "user", "principal": "bob"}])
    assert client.get(f"/handoffs/{m['code']}", headers=as_bob).status_code == 404


def test_list_never_includes_others(client, as_ana, as_bob, ana, bob):
    mk(client, as_ana, audience="authenticated")
    assert client.get("/handoffs", headers=as_bob).json() == []


def test_notes_cap_and_concurrent_append(client, as_ana, db_session, ana):
    m = mk(client, as_ana)
    p = principal_for_user(db_session, ana)
    handoff.append_note(db_session, p, m["code"], "one")
    handoff.append_note(db_session, p, m["code"], "two")
    db_session.expire_all()
    assert [n["text"] for n in db_session.get(Handoff, m["code"]).notes] == ["one", "two"]
    db_session.query(Handoff).update({"notes": [{"text": str(i)} for i in range(199)]})
    db_session.flush()
    assert (
        client.post(f"/handoffs/{m['code']}/notes", json={"text": "200th"}, headers=as_ana).status_code == 201
    )
    r = client.post(f"/handoffs/{m['code']}/notes", json={"text": "201st"}, headers=as_ana)
    assert r.status_code == 409 and r.json()["detail"] == "note limit reached"
    got = client.get(f"/handoffs/{m['code']}", headers=as_ana).json()
    assert len(got["notes"]) == 20 and got["notes"][-1]["text"] == "200th" and got["parts"]["notes"] == 10


def test_size_caps_422(client, as_ana, ana):
    for body in (
        {"title": "x" * 201}, {"goal": "x" * 2001}, {"brief": "x" * 20001},
        {"doc_ids": ["a"] * 501}, {"audience": "shared", "shares": [{"kind": "user", "principal": "a"}] * 101},
    ):  # fmt: skip
        assert post(client, as_ana, **body).status_code == 422, list(body)
    m = mk(client, as_ana)
    assert (
        client.patch(f"/handoffs/{m['code']}", json={"title": "x" * 201}, headers=as_ana).status_code == 422
    )
    assert (
        client.post(f"/handoffs/{m['code']}/notes", json={"text": "x" * 10001}, headers=as_ana).status_code
        == 422
    )


def test_normalize_query_fragment_and_non_ascii():
    assert handoff.normalize("https://h/h/7KQ2-XM9P?x=1#frag") == "7KQ2XM9P"
    for bad in ("7KQ2XM9\u0131", "\uff17KQ2XM9P"):  # dotless i / fullwidth 7
        with pytest.raises(Exception) as e:
            handoff.normalize(bad)
        assert e.value.status_code == 404


def test_collision_only_on_pk(monkeypatch, db_session, ana):
    from sqlalchemy.exc import IntegrityError

    p = principal_for_user(db_session, ana)

    class Orig:
        class diag:
            constraint_name = "ck_handoffs_kind"

    def boom(*a, **k):
        raise IntegrityError("x", {}, Orig())

    monkeypatch.setattr(db_session, "begin_nested", boom)
    with pytest.raises(IntegrityError):
        handoff.create(db_session, p, "agent")


def test_patch_audience_change_drops_shares(client, as_ana, as_bob, db_session, ana, bob):
    from roracle.models import HandoffShare

    m = mk(client, as_ana, audience="shared", shares=[{"kind": "group", "principal": "/Physics"}])
    client.patch(f"/handoffs/{m['code']}", json={"audience": "authenticated"}, headers=as_ana)
    assert db_session.query(HandoffShare).count() == 0
    client.patch(f"/handoffs/{m['code']}", json={"audience": "creator"}, headers=as_ana)
    assert client.get(f"/handoffs/{m['code']}", headers=as_bob).status_code == 404


def test_collection_non_owner_404(client, as_ana, as_bob, db_session, ana, bob):
    col = Collection(owner_id=ana, name="mine")
    db_session.add(col)
    db_session.flush()
    assert post(client, as_bob, kind="collection", source_id=str(col.id)).status_code == 404


def test_patch_shares_without_shared_audience_422(client, as_ana, ana):
    m = mk(client, as_ana)
    r = client.patch(
        f"/handoffs/{m['code']}", json={"shares": [{"kind": "group", "principal": "/P"}]}, headers=as_ana
    )
    assert r.status_code == 422


def test_read_pat_cannot_mutate_and_outsider_note_404(client, as_ana, as_bob, ana, bob):
    m = mk(client, as_ana)
    c = m["code"]
    tok = bearer(
        client.post("/me/tokens", json={"name": "r", "scopes": ["read"]}, headers=as_ana).json()["token"]
    )
    assert client.post(f"/handoffs/{c}/notes", json={"text": "x"}, headers=tok).status_code == 403
    assert client.patch(f"/handoffs/{c}", json={"title": "x"}, headers=tok).status_code == 403
    assert client.delete(f"/handoffs/{c}", headers=tok).status_code == 403
    assert client.post(f"/handoffs/{c}/notes", json={"text": "x"}, headers=as_bob).status_code == 404
