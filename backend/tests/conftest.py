import json
import os
import time
import uuid
from pathlib import Path

import boto3
import jwt
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from roracle.api.app import create_app
from roracle.auth import JwksCache, get_jwks
from roracle.config import Settings
from roracle.db import get_session, get_stream_session, make_session_factory
from roracle.models import Base, Blob, Document, User
from roracle.storage import BlobStore, stage

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
    db = make_url(settings.database_url).database or ""
    if not db.endswith("_test"):
        pytest.exit(f"refusing to run tests against non-test database {db}", returncode=2)
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
        session.execute(  # the migration's seed row; documents.label_id defaults to it
            text(
                "INSERT INTO labels (id, name, colour, rank, is_default) VALUES (gen_random_uuid(), 'General', '#496580', 0, true)"
            )
        )
        session.commit()
        yield session
        session.rollback()


@pytest.fixture
def app(settings, db_session):
    """App whose get_session yields the test's db_session (the fixture owns the transaction)."""
    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_stream_session] = lambda: db_session  # one transaction owner in tests
    return app


@pytest.fixture
def _blob_store_override(app, store):
    from roracle.api.routes.documents import get_blob_store

    app.dependency_overrides[get_blob_store] = lambda: store


@pytest.fixture
def settings_small_limit(app, settings):
    from roracle.auth import get_app_settings

    small = settings.model_copy(update={"max_upload_bytes": 1024 * 1024})
    app.dependency_overrides[get_app_settings] = lambda: small
    return small


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
    return {
        "keys": [
            {"kid": "enc1", "kty": "RSA", "alg": "RSA-OAEP", "use": "enc", "n": "AQAB", "e": "AQAB"},
            {"kid": "bad1", "kty": "RSA", "use": "sig", "alg": "RS256", "n": "!!", "e": "x"},
            {**jwk, "kid": "k1", "alg": "RS256", "use": "sig"},
        ]
    }


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
        typ="Bearer",
        azp="roracle-web",
        **extra,
    ):
        claims = {
            "sub": sub,
            "preferred_username": username,
            "groups": list(groups),
            "realm_access": {"roles": list(roles)},
            "exp": int(time.time()) + exp_in,
            "iss": iss or settings.oidc_issuer,
            "aud": aud,
            "typ": typ,
            "azp": azp,
            **extra,
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


class S3Helper:
    def __init__(self, client, bucket):
        self.client, self.bucket = client, bucket

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False

    def keys(self) -> list[str]:
        pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket)
        return [o["Key"] for p in pages for o in p.get("Contents", [])]


@pytest.fixture(scope="session")
def s3_client(settings):
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="us-east-1",
        config=boto3.session.Config(s3={"addressing_style": "path"}),
    )


@pytest.fixture(scope="session")
def test_bucket(s3_client):
    name = f"roracle-test-{uuid.uuid4().hex[:8]}"
    s3_client.create_bucket(Bucket=name)
    yield name
    helper = S3Helper(s3_client, name)
    for key in helper.keys():  # noqa: SIM118
        s3_client.delete_object(Bucket=name, Key=key)
    s3_client.delete_bucket(Bucket=name)


@pytest.fixture
def store(s3_client, test_bucket):
    return BlobStore(s3_client, test_bucket)


@pytest.fixture
def s3(s3_client, test_bucket):
    return S3Helper(s3_client, test_bucket)


@pytest.fixture
def staged():
    import io

    s = stage(io.BytesIO(uuid.uuid4().bytes * 4), max_bytes=1024)
    yield s
    s.path.unlink(missing_ok=True)


@pytest.fixture
def ana(db_session, client, as_ana):
    from api_seed import user_id

    return user_id(db_session, client, as_ana, "ana")


@pytest.fixture
def bob(db_session, client, as_bob):
    from api_seed import user_id

    return user_id(db_session, client, as_bob, "bob")


@pytest.fixture
def cfg(settings):
    """Settings with a unique fake embed model (4 dims) so each test gets its own ES index."""
    return settings.model_copy(
        update={"embed_model": f"fake-embed-{uuid.uuid4().hex[:8]}", "embed_dims": 4, "grobid_url": ""}
    )


@pytest.fixture
def es(cfg):
    from elasticsearch import Elasticsearch

    from roracle.search_index import index_name

    c = Elasticsearch(cfg.elastic_url)
    yield c
    c.indices.delete(index=index_name(cfg.embed_model), ignore_unavailable=True)


@pytest.fixture
def adm(make_token):
    return bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))


@pytest.fixture
def owner(db_session):
    u = User(sub="adm", username="adm", is_admin=True)
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def prefix(s3_client, test_bucket):
    p = f"src-{uuid.uuid4().hex[:8]}/"
    yield p
    for o in s3_client.list_objects_v2(Bucket=test_bucket, Prefix=p).get("Contents", []):
        s3_client.delete_object(Bucket=test_bucket, Key=o["Key"])


@pytest.fixture(autouse=True)
def _clear_search_caches():
    """Process-wide memo/page caches must never leak between tests."""
    from roracle import retrieval, search_fields
    from roracle.ingest import neardup
    from roracle.search_cache import PAGE_CACHE

    neardup._ready = False  # the LSH-ready marker lives in app_settings (truncated per test)
    retrieval._EMBEDS.clear()
    PAGE_CACHE.clear()
    search_fields._READY.clear()  # fields_ready memo: per index name, so per test
    yield
