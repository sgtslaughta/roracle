import httpx
import jwt
import pytest
from conftest import bearer
from cryptography.hazmat.primitives import serialization
from sqlalchemy import func, select

from roracle.auth import JwksCache
from roracle.models import User

BAD = {"detail": "invalid token"}


def assert_401(r):
    assert r.status_code == 401
    assert r.json() == BAD
    assert r.headers["www-authenticate"] == "Bearer"


def test_me_returns_principal_and_creates_user(client, make_token, db_session):
    r = client.get("/me", headers=bearer(make_token(groups=("/Math",), roles=("roracle-admin",))))
    assert r.status_code == 200 and r.json()["groups"] == ["/Math"] and r.json()["is_admin"] is True
    assert r.json()["username"] == "ana"
    assert db_session.scalar(select(func.count()).select_from(User)) == 1


def test_non_admin(client, make_token):
    r = client.get("/me", headers=bearer(make_token(roles=("other",))))
    assert r.json()["is_admin"] is False


def test_missing_token_is_401(client, jwks):
    assert_401(client.get("/me"))


def test_non_bearer_scheme_is_401(client, jwks, make_token):
    assert_401(client.get("/me", headers={"Authorization": f"Basic {make_token()}"}))


def test_garbage_token_is_401(client, jwks):
    assert_401(client.get("/me", headers=bearer("not.a.jwt")))


def test_expired_token_is_401(client, make_token):
    assert_401(client.get("/me", headers=bearer(make_token(exp_in=-60))))


def test_wrong_audience_is_401(client, make_token):
    assert_401(client.get("/me", headers=bearer(make_token(aud="other"))))


def test_wrong_issuer_is_401(client, make_token):
    assert_401(client.get("/me", headers=bearer(make_token(iss="http://evil/realms/x"))))


def test_hs256_token_is_rejected(client, make_token):
    t = make_token(key="s" * 40, alg="HS256")
    assert_401(client.get("/me", headers=bearer(t)))


def test_alg_none_is_rejected(client, make_token, settings):
    t = jwt.encode(
        {"sub": "x", "iss": settings.oidc_issuer, "aud": "roracle", "exp": 9999999999},
        None,
        algorithm="none",
        headers={"kid": "k1"},
    )
    assert_401(client.get("/me", headers=bearer(t)))


def test_missing_sub_is_401(client, rsa_key, settings, jwks):
    t = jwt.encode(
        {"iss": settings.oidc_issuer, "aud": "roracle", "exp": 9999999999},
        rsa_key,
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    assert_401(client.get("/me", headers=bearer(t)))


def test_unknown_kid_refetches_jwks_once(jwks_with_counter, client, make_token):
    assert_401(client.get("/me", headers=bearer(make_token(kid="rotated"))))
    assert jwks_with_counter.fetches == 2  # initial + one refetch, no more


def test_unknown_kid_refetch_is_rate_limited(jwks_with_counter, client, make_token):
    for _ in range(3):
        assert_401(client.get("/me", headers=bearer(make_token(kid="rotated"))))
    assert jwks_with_counter.fetches == 2


def test_second_login_updates_username_not_duplicates(client, make_token, db_session):
    r1 = client.get("/me", headers=bearer(make_token(sub="s", username="ana", email="a@x.org")))
    r2 = client.get("/me", headers=bearer(make_token(sub="s", username="ana.r")))
    assert r1.status_code == r2.status_code == 200
    users = db_session.scalars(select(User)).all()
    assert len(users) == 1
    assert users[0].username == "ana.r" and users[0].email == "a@x.org"


def test_as_ana_fixture(client, as_ana):
    r = client.get("/me", headers=as_ana)
    assert r.json()["groups"] == ["/Mathematics/Spectral Theory"]


def test_leeway_accepts_recently_expired(client, make_token):
    assert client.get("/me", headers=bearer(make_token(exp_in=-10))).status_code == 200


def test_aud_list_containing_audience(client, make_token):
    assert client.get("/me", headers=bearer(make_token(aud=["other", "roracle"]))).status_code == 200


def test_empty_sub_is_401(client, make_token):
    assert_401(client.get("/me", headers=bearer(make_token(sub=""))))


def test_lowercase_bearer_scheme(client, make_token):
    r = client.get("/me", headers={"Authorization": f"bearer {make_token()}"})
    assert r.status_code == 200


def test_non_list_roles_is_401(client, make_token):
    assert_401(client.get("/me", headers=bearer(make_token(realm_access={"roles": "roracle-admin"}))))


def test_id_token_typ_is_401(client, make_token):
    assert_401(client.get("/me", headers=bearer(make_token(typ="ID"))))


def test_foreign_azp_is_401(client, make_token):
    assert_401(client.get("/me", headers=bearer(make_token(azp="other-client"))))


def test_allowed_clients_env_formats():
    from roracle.config import Settings

    base = {k: "x" for k in ("database_url", "s3_endpoint", "s3_access_key", "s3_secret_key")}
    base |= {"oidc_issuer": "x", "oidc_jwks_url": "x"}
    assert Settings(**base, oidc_allowed_clients="a, b").oidc_allowed_clients == ["a", "b"]
    assert Settings(**base, oidc_allowed_clients='["a","b"]').oidc_allowed_clients == ["a", "b"]


def test_hs256_signed_with_rsa_public_pem_is_rejected(client, make_token, rsa_key):
    pem = rsa_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    # PyJWT refuses HMAC with a PEM key; build the token by hand to emulate the attack.
    import base64
    import hashlib
    import hmac
    import json
    import time

    def b64(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=")

    head = b64(json.dumps({"alg": "HS256", "kid": "k1"}).encode())
    body = b64(
        json.dumps(
            {
                "sub": "x",
                "iss": "http://localhost:8080/realms/roracle",
                "aud": "roracle",
                "exp": int(time.time()) + 300,
                "typ": "Bearer",
                "azp": "roracle-web",
            }
        ).encode()
    )
    sig = b64(hmac.new(pem, head + b"." + body, hashlib.sha256).digest())
    assert_401(client.get("/me", headers=bearer((head + b"." + body + b"." + sig).decode())))


def test_jwks_fetch_error_is_401(app, client, make_token):
    token = make_token()

    def boom(_url):
        raise httpx.ConnectError("down")

    from roracle.auth import get_jwks

    app.dependency_overrides[get_jwks] = lambda: JwksCache("x", fetch=boom)
    assert_401(client.get("/me", headers=bearer(token)))


def test_enc_and_malformed_keys_are_skipped(jwks):
    assert jwks.key_for("k1")
    for kid in ("enc1", "bad1"):
        with pytest.raises(KeyError):
            jwks.key_for(kid)


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def _cache(jwk_set, clock, fail=False):
    calls = []

    def fetch(_url):
        calls.append(1)
        if fail:
            raise httpx.ConnectError("down")
        return jwk_set

    return JwksCache("u", fetch=fetch, clock=clock), calls


def test_refetch_throttle_lifts_after_60s(jwk_set):
    clock = Clock()
    cache, calls = _cache(jwk_set, clock)
    for _ in range(2):
        with pytest.raises(KeyError):
            cache.key_for("nope")
    assert len(calls) == 2
    clock.t += 61
    with pytest.raises(KeyError):
        cache.key_for("nope")
    assert len(calls) == 3


def test_initial_load_failure_is_throttled(jwk_set):
    clock = Clock()
    cache, calls = _cache(jwk_set, clock, fail=True)
    for _ in range(3):
        with pytest.raises(Exception):  # noqa: B017
            cache.key_for("k1")
    assert len(calls) == 1
    clock.t += 61
    with pytest.raises(Exception):  # noqa: B017
        cache.key_for("k1")
    assert len(calls) == 2


def test_keys_expire_after_10min_and_hard_max(jwk_set):
    clock = Clock()
    cache, calls = _cache(jwk_set, clock)
    assert cache.key_for("k1")
    clock.t += 601
    assert cache.key_for("k1") and len(calls) == 2  # stale -> reloaded
    cache._fetch = lambda _u: (_ for _ in ()).throw(httpx.ConnectError("down"))
    clock.t += 700
    assert cache.key_for("k1")  # reload fails, old keys still served
    clock.t += 3000  # beyond 1 h since last good load
    with pytest.raises(Exception):  # noqa: B017
        cache.key_for("k1")


def _keycloak_token(settings):
    try:
        r = httpx.post(
            settings.oidc_issuer + "/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "roracle-web",
                "username": "researcher",
                "password": "researcher",
            },
            timeout=3,
        )
    except httpx.HTTPError:
        pytest.skip("dev Keycloak unreachable")
    if r.status_code != 200:
        pytest.skip(f"dev Keycloak token request failed: {r.status_code}")
    return r.json()["access_token"]


@pytest.mark.integration
def test_real_keycloak_token_with_real_jwks(client, settings):
    r = client.get("/me", headers=bearer(_keycloak_token(settings)))
    assert r.status_code == 200
    assert r.json()["groups"] == ["/Mathematics/Spectral Theory"]
