"""foundation schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sub", sa.String, nullable=False, unique=True),
        sa.Column("username", sa.String, nullable=False),
        sa.Column("email", sa.String),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "blobs",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("refcount", sa.Integer, server_default="1", nullable=False),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("org_unit", sa.String, nullable=False),
        sa.Column("visibility", sa.String, nullable=False),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", TZ),
        sa.CheckConstraint(
            "visibility IN ('private','shared','org_unit','everyone')", name="ck_documents_visibility"
        ),
    )
    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("blob_sha256", sa.String, sa.ForeignKey("blobs.sha256"), nullable=False),
        sa.Column("filename", sa.String, nullable=False),
        sa.Column("content_type", sa.String, nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", "version", name="uq_document_versions_doc_version"),
    )
    op.create_table(
        "document_shares",
        sa.Column(
            "document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), primary_key=True
        ),
        sa.Column("principal_kind", sa.String, primary_key=True),
        sa.Column("principal", sa.String, primary_key=True),
        sa.CheckConstraint("principal_kind IN ('user','group')", name="ck_document_shares_kind"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String, server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer, server_default="0", nullable=False),
        sa.Column("run_after", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("last_error", sa.String),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('queued','running','done','failed')", name="ck_jobs_status"),
    )
    op.create_index("ix_jobs_status_run_after", "jobs", ["status", "run_after"])


def downgrade() -> None:
    for t in ("jobs", "document_shares", "document_versions", "documents", "blobs", "users"):
        op.drop_table(t)
