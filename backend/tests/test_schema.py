import re

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from roracle.models import Document, DocumentVersion


def test_migrations_create_all_tables(db_session):
    names = set(inspect(db_session.get_bind()).get_table_names())
    assert {"users", "blobs", "documents", "document_versions", "document_shares", "jobs"} <= names


def test_visibility_is_constrained(db_session, user):
    db_session.add(Document(owner_id=user.id, title="t", org_unit="/", visibility="public"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_version_numbers_are_unique_per_document(db_session, document, blob):
    db_session.add_all(
        [
            DocumentVersion(
                document_id=document.id,
                version=1,
                blob_sha256=blob.sha256,
                filename="a",
                content_type="x",
                size=1,
            )
            for _ in range(2)
        ]
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_models_match_migrations(db_session):
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from roracle.models import Base

    ctx = MigrationContext.configure(
        db_session.connection(), opts={"compare_server_default": True, "compare_type": True}
    )
    assert compare_metadata(ctx, Base.metadata) == []


@pytest.mark.parametrize(
    ("table", "values"),
    [
        ("documents", {"private", "shared", "org_unit", "everyone"}),
        ("jobs", {"queued", "running", "done", "failed"}),
        ("document_shares", {"user", "group"}),
    ],
)
def test_check_constraints_have_exact_values(db_session, table, values):
    checks = inspect(db_session.get_bind()).get_check_constraints(table)
    assert len(checks) == 1
    assert set(re.findall(r"'([^']+)'", checks[0]["sqltext"])) == values


def test_hot_path_indexes_exist(db_session):
    rows = db_session.execute(text("SELECT indexname FROM pg_indexes")).scalars().all()
    assert {"ix_documents_owner_id", "ix_document_versions_blob_sha256", "ix_jobs_version_id"} <= set(rows)
