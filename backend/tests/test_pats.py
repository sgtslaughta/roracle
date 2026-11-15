import hashlib
from datetime import UTC, datetime, timedelta

from conftest import bearer

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
