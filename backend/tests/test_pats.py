import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from conftest import bearer
from sqlalchemy import func, text

from roracle import pats
from roracle.auth import get_app_settings
from roracle.models import PersonalAccessToken, User


def mint(client, as_ana, **body):
    r = client.post("/me/tokens", json={"name": "cli", **body}, headers=as_ana)
    assert r.status_code == 201, r.text
    return r.json()


def test_pat_authenticates_with_stored_groups(client, as_ana):
    tok = mint(client, as_ana)["token"]
    assert tok.startswith("ror_pat_")
    r = client.get("/me", headers=bearer(tok))
    assert r.status_code == 200
    assert r.json()["username"] == "ana"
    assert r.json()["groups"] == ["/Mathematics/Spectral Theory"]


def test_last_used_updated(client, as_ana, db_session):
    tok = mint(client, as_ana)["token"]
    assert db_session.query(PersonalAccessToken).one().last_used_at is None
    client.get("/me", headers=bearer(tok))
    assert db_session.query(PersonalAccessToken).one().last_used_at is not None


def test_read_pat_cannot_write(client, as_ana):
    tok = bearer(mint(client, as_ana)["token"])
    r = client.post("/collections", json={"name": "x"}, headers=tok)
    assert r.status_code == 403 and r.json()["detail"] == "insufficient scope"
    assert client.get("/collections", headers=tok).status_code == 200
    assert (
        client.delete(
            "/collections/00000000-0000-0000-0000-000000000000/items/00000000-0000-0000-0000-000000000000",
            headers=tok,
        ).status_code
        == 403
    )


def test_write_pat_can_write(client, as_ana):
    tok = bearer(mint(client, as_ana, scopes=["read", "write"])["token"])
    assert client.post("/collections", json={"name": "x"}, headers=tok).status_code == 201


def test_write_only_scope_normalised(client, as_ana):
    assert mint(client, as_ana, scopes=["write"])["scopes"] == ["read", "write"]
    assert mint(client, as_ana, scopes=["read", "read"])["scopes"] == ["read"]


def test_bad_scope_or_empty_scopes_422(client, as_ana):
    for scopes in (["admin"], []):
        r = client.post("/me/tokens", json={"name": "c", "scopes": scopes}, headers=as_ana)
        assert r.status_code == 422


def _fail_body(client, tok):
    r = client.get("/me", headers=bearer(tok))
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Bearer"
    return r.json()


def test_failures_are_uniform_401(client, as_ana, db_session, app, settings):
    revoked = mint(client, as_ana)
    assert client.delete(f"/me/tokens/{revoked['id']}", headers=as_ana).status_code == 204
    expired = mint(client, as_ana)
    db_session.query(PersonalAccessToken).filter_by(id=expired["id"]).update(
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    db_session.flush()
    live = mint(client, as_ana)["token"]
    bodies = [
        _fail_body(client, revoked["token"]),
        _fail_body(client, expired["token"]),
        _fail_body(client, "ror_pat_unknownunknownunknown"),
        _fail_body(client, "ror_pat_"),
        _fail_body(client, "garbage"),
    ]
    app.dependency_overrides[get_app_settings] = lambda: settings.model_copy(update={"pats_enabled": False})
    bodies.append(_fail_body(client, live))
    assert all(b == bodies[0] for b in bodies)


def test_pat_cannot_manage_tokens(client, as_ana):
    tok = bearer(mint(client, as_ana, scopes=["read", "write"])["token"])
    for call in (
        lambda: client.get("/me/tokens", headers=tok),
        lambda: client.post("/me/tokens", json={"name": "n"}, headers=tok),
        lambda: client.delete("/me/tokens/00000000-0000-0000-0000-000000000000", headers=tok),
    ):
        r = call()
        assert r.status_code == 403
        assert r.json()["detail"] == "personal access tokens cannot manage tokens"


def test_hash_stored_not_token(client, as_ana, db_session):
    out = mint(client, as_ana)
    row = db_session.query(PersonalAccessToken).one()
    assert row.token_hash == hashlib.sha256(out["token"].encode()).hexdigest()
    assert row.prefix == out["token"][:12] == out["prefix"]
    assert out["token"] not in {row.token_hash, row.prefix, row.name}
    listing = client.get("/me/tokens", headers=as_ana)
    assert out["token"] not in listing.text and row.token_hash not in listing.text
    assert "token" not in listing.json()[0]
    assert set(listing.json()[0]) == {
        "id", "name", "prefix", "scopes", "expires_at", "revoked_at", "last_used_at", "created_at",
    }  # fmt: skip


def test_expiry_bounds(client, as_ana):
    for days in (366, 0):
        r = client.post("/me/tokens", json={"name": "c", "expires_in_days": days}, headers=as_ana)
        assert r.status_code == 422
    assert client.post("/me/tokens", json={"name": ""}, headers=as_ana).status_code == 422
    assert client.post("/me/tokens", json={"name": "x" * 101}, headers=as_ana).status_code == 422
    assert mint(client, as_ana, expires_in_days=365)


def test_list_newest_first_and_revoke_idempotent(client, as_ana):
    a, b = mint(client, as_ana, name="a"), mint(client, as_ana, name="b")
    assert [t["name"] for t in client.get("/me/tokens", headers=as_ana).json()] == ["b", "a"]
    for _ in range(2):
        assert client.delete(f"/me/tokens/{a['id']}", headers=as_ana).status_code == 204
    listed = {t["name"]: t for t in client.get("/me/tokens", headers=as_ana).json()}
    assert listed["a"]["revoked_at"] and listed["b"]["revoked_at"] is None
    assert b["id"] in {t["id"] for t in listed.values()}


def test_other_users_token_404(client, as_ana, as_bob):
    t = mint(client, as_ana)
    r = client.delete(f"/me/tokens/{t['id']}", headers=as_bob)
    assert r.status_code == 404 and r.json() == {"detail": "not found"}
    assert client.delete("/me/tokens/not-a-uuid", headers=as_bob).status_code == 404
    assert client.get("/me/tokens", headers=as_bob).json() == []
    assert client.get("/me", headers=bearer(t["token"])).status_code == 200  # not revoked


def test_deleted_user_pat_401(client, as_ana, db_session):
    tok = mint(client, as_ana)["token"]
    db_session.query(PersonalAccessToken).delete()  # cascade would drop it; simulate orphan-free user removal
    db_session.query(User).delete()
    db_session.flush()
    assert client.get("/me", headers=bearer(tok)).status_code == 401


def test_jwt_principal_is_full_scope(client, as_ana):
    # JWT path unchanged: POST works and token management allowed
    assert client.post("/collections", json={"name": "x"}, headers=as_ana).status_code == 201
    assert client.get("/me/tokens", headers=as_ana).status_code == 200


# --- fix round 1: identity bounds, admin, non-blocking usage writes ---

ZERO = "00000000-0000-0000-0000-000000000000"


def _set_user(db_session, **vals):
    db_session.query(User).update(vals)
    db_session.flush()
    db_session.expire_all()


def test_read_pat_scope_on_patch_put_and_head(client, as_ana):
    tok = bearer(mint(client, as_ana)["token"])
    assert client.patch(f"/documents/{ZERO}", json={}, headers=tok).status_code == 403
    assert client.put(f"/collections/{ZERO}/items/{ZERO}", headers=tok).status_code == 403
    assert client.head("/me", headers=tok).status_code in (200, 405)
    assert client.head("/me", headers=tok).status_code != 403


def test_expires_at_now_rejected(client, as_ana, db_session):
    t = mint(client, as_ana)
    db_session.query(PersonalAccessToken).update({"expires_at": func.now()})
    db_session.flush()
    assert client.get("/me", headers=bearer(t["token"])).status_code == 401


def test_failed_auth_leaves_last_used_null(client, as_ana, db_session):
    t = mint(client, as_ana)
    client.delete(f"/me/tokens/{t['id']}", headers=as_ana)
    assert client.get("/me", headers=bearer(t["token"])).status_code == 401
    assert db_session.query(PersonalAccessToken).one().last_used_at is None


def test_lookup_error_is_401_other_errors_not(client, as_ana, monkeypatch):
    tok = bearer(mint(client, as_ana)["token"])

    def boom(*a):
        raise LookupError("x")

    monkeypatch.setattr(pats, "principal_for_user", boom)
    assert client.get("/me", headers=tok).status_code == 401

    def db_down(*a):
        raise RuntimeError("db down")

    monkeypatch.setattr(pats, "principal_for_user", db_down)
    with pytest.raises(RuntimeError):
        client.get("/me", headers=tok)


def test_groups_snapshot_intersect_current(client, as_ana, db_session):
    tok = bearer(mint(client, as_ana)["token"])
    assert db_session.query(PersonalAccessToken).one().groups == ["/Mathematics/Spectral Theory"]
    _set_user(db_session, groups=["/Physics", "/Mathematics/Spectral Theory"])
    assert client.get("/me", headers=tok).json()["groups"] == ["/Mathematics/Spectral Theory"]  # no widening
    _set_user(db_session, groups=["/Physics"])
    assert client.get("/me", headers=tok).json()["groups"] == []  # narrowing applies


def test_pat_never_admin(client, make_token, db_session):
    admin = bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))
    assert client.get("/admin/needs-title", headers=admin).status_code == 200
    tok = bearer(mint(client, admin, scopes=["read", "write"])["token"])
    assert client.get("/me", headers=tok).json()["is_admin"] is False
    assert client.get("/admin/needs-title", headers=tok).status_code == 403


def test_stale_and_null_login_401_with_detail(client, as_ana, db_session):
    tok = bearer(mint(client, as_ana)["token"])
    assert client.get("/me", headers=tok).status_code == 200  # JWT login stamped last_login_at
    _set_user(db_session, last_login_at=datetime.now(UTC) - timedelta(days=31))
    r = client.get("/me", headers=tok)
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == "Bearer"
    assert r.json()["detail"] == "token owner session stale — sign in again"
    _set_user(db_session, last_login_at=None)
    assert client.get("/me", headers=tok).status_code == 401
    _set_user(db_session, last_login_at=datetime.now(UTC) - timedelta(days=29))
    assert client.get("/me", headers=tok).status_code == 200
    assert client.get("/me", headers=as_ana).status_code == 200  # JWT login refreshes
    assert db_session.query(User).one().last_login_at > datetime.now(UTC) - timedelta(minutes=1)


def test_stale_detail_only_for_valid_tokens(client, as_ana, db_session):
    _set_user(db_session, last_login_at=None)
    assert client.get("/me", headers=bearer("ror_pat_nope")).json()["detail"] == "invalid token"


def test_admin_revokes_all_user_pats(client, as_ana, as_bob, make_token, db_session):
    t1, t2 = mint(client, as_ana)["token"], mint(client, as_ana)["token"]
    uid = client.get("/me", headers=as_ana).json()["id"]
    assert client.delete(f"/admin/users/{uid}/pats", headers=as_bob).status_code == 403
    assert client.get("/me", headers=bearer(t1)).status_code == 200
    admin = bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))
    assert client.delete(f"/admin/users/{uid}/pats", headers=admin).status_code == 204
    for t in (t1, t2):
        assert client.get("/me", headers=bearer(t)).status_code == 401
    assert client.get("/me/tokens", headers=as_ana).json()[0]["revoked_at"]
    # a PAT can never reach it
    pat = bearer(mint(client, as_ana, scopes=["read", "write"])["token"])
    assert client.delete(f"/admin/users/{uid}/pats", headers=pat).status_code == 403


def test_last_used_does_not_block_on_locked_row(client, as_ana, session_factory):
    tok = bearer(mint(client, as_ana)["token"])
    with session_factory() as other:
        other.execute(text("SELECT 1 FROM personal_access_tokens FOR UPDATE"))
        assert client.get("/me", headers=tok).status_code == 200
        other.rollback()
