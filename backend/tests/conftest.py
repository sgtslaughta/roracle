import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from roracle.api.app import create_app
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
