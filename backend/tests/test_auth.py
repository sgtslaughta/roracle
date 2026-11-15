import jwt
from conftest import bearer
from sqlalchemy import func, select

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
    client.get("/me", headers=bearer(make_token(sub="s", username="ana", email="a@x.org")))
    client.get("/me", headers=bearer(make_token(sub="s", username="ana.r")))
    users = db_session.scalars(select(User)).all()
    assert len(users) == 1
    assert users[0].username == "ana.r" and users[0].email == "a@x.org"


def test_as_ana_fixture(client, as_ana):
    r = client.get("/me", headers=as_ana)
    assert r.json()["groups"] == ["/Mathematics/Spectral Theory"]
