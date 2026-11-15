import json
import os
import time
import uuid
from pathlib import Path

import jwt
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import text

from roracle.api.app import create_app
from roracle.auth import JwksCache, get_jwks
from roracle.config import Settings
from roracle.db import get_session, make_session_factory
from roracle.models import Base, Blob, Document, User

REPO_ROOT = Path(__file__).parents[2]
BACKEND_ROOT = Path(__file__).parents[1]

os.environ.setdefault("ROR_DATABASE_URL", "postgresql+psycopg://roracle:roracle@localhost:55433/roracle_test")
os.environ.setdefault("ROR_S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("ROR_S3_ACCESS_KEY", "minioadmin")
os.environ.setdefault("ROR_S3_SECRET_KEY", "minioadmin")
os.environ.setdefault("ROR_OIDC_ISSUER", "http://localhost:8080/realms/roracle")
os.environ.setdefault(
    "ROR_OIDC_JWKS_URL", "http://localhost:8080/realms/roracle/protocol/openid-connect/certs"
)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def session_factory(settings):
    command.upgrade(_alembic_config(), "head")
    return make_session_factory(settings.database_url)


def _alembic_config() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    return cfg


@pytest.fixture
def db_session(session_factory):
    with session_factory() as session:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        session.commit()
        yield session
        session.rollback()


@pytest.fixture
def app(settings, db_session):
    """App whose get_session yields the test's db_session (the fixture owns the transaction)."""
    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: db_session
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def user(db_session):
    u = User(id=uuid.uuid4(), sub="sub-1", username="researcher", email="r@example.org")
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture
def blob(db_session):
    b = Blob(sha256="a" * 64, size=1)
    db_session.add(b)
    db_session.flush()
    return b


@pytest.fixture
def document(db_session, user):
    d = Document(owner_id=user.id, title="t", org_unit="/", visibility="private")
    db_session.add(d)
    db_session.flush()
    return d


@pytest.fixture(scope="session")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def jwk_set(rsa_key):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key()))
    return {"keys": [{**jwk, "kid": "k1", "alg": "RS256", "use": "sig"}]}


class CountingJwks(JwksCache):
    fetches = 0


@pytest.fixture
def jwks(app, jwk_set):
    cache = CountingJwks("test://jwks", fetch=lambda _url: jwk_set)
    real_fetch = cache._fetch

    def counting(url):
        cache.fetches += 1
        return real_fetch(url)

    cache._fetch = counting
    app.dependency_overrides[get_jwks] = lambda: cache
    return cache


@pytest.fixture
def jwks_with_counter(jwks):
    return jwks


@pytest.fixture
def make_token(rsa_key, settings, jwks):
    def make(
        sub="u1",
        username="ana",
        groups=(),
        roles=(),
        exp_in=300,
        aud="roracle",
        kid="k1",
        iss=None,
        key=None,
        alg="RS256",
        email=None,
    ):
        claims = {
            "sub": sub,
            "preferred_username": username,
            "groups": list(groups),
            "realm_access": {"roles": list(roles)},
            "exp": int(time.time()) + exp_in,
            "iss": iss or settings.oidc_issuer,
            "aud": aud,
        }
        if email:
            claims["email"] = email
        return jwt.encode(claims, key or rsa_key, algorithm=alg, headers={"kid": kid})

    return make


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def as_ana(make_token):
    return bearer(make_token(sub="ana", username="ana", groups=("/Mathematics/Spectral Theory",)))


@pytest.fixture
def as_bob(make_token):
    return bearer(make_token(sub="bob", username="bob", groups=("/Physics",)))
