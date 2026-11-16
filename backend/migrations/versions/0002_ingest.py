"""ingest state

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

STAGES = (
    "'uploaded','scanned','parsed','summarized','deduped','chunked','embedded',"
    "'searchable','review','needs_title','rejected','cancelled'"
)


def upgrade() -> None:
    op.alter_column("documents", "title", existing_type=sa.String, nullable=True)
    op.add_column("users", sa.Column("groups", postgresql.JSONB, server_default="[]", nullable=False))
    op.add_column("users", sa.Column("is_admin", sa.Boolean, server_default="false", nullable=False))
    t = "document_versions"
    op.add_column(t, sa.Column("stage", sa.String, server_default="uploaded", nullable=False))
    op.add_column(t, sa.Column("page_count", sa.Integer))
    op.add_column(t, sa.Column("summary", sa.String))
    op.add_column(t, sa.Column("meta", postgresql.JSONB, server_default="{}", nullable=False))
    op.add_column(t, sa.Column("references", postgresql.JSONB, server_default="[]", nullable=False))
    op.add_column(t, sa.Column("minhash", sa.LargeBinary))
    op.add_column(
        t, sa.Column("near_dup_of", postgresql.UUID(as_uuid=True), sa.ForeignKey("document_versions.id"))
    )
    op.add_column(t, sa.Column("is_latest", sa.Boolean, server_default="true", nullable=False))
    op.add_column(t, sa.Column("ingest_attempts", sa.Integer, server_default="0", nullable=False))
    op.create_check_constraint("ck_document_versions_stage", t, f"stage IN ({STAGES})")


def downgrade() -> None:
    t = "document_versions"
    op.drop_constraint("ck_document_versions_stage", t)
    for c in (
        "ingest_attempts", "is_latest", "near_dup_of", "minhash", "references", "meta", "summary",
        "page_count", "stage",
    ):  # fmt: skip
        op.drop_column(t, c)
    op.execute("UPDATE documents SET title = '' WHERE title IS NULL")
    op.alter_column("documents", "title", existing_type=sa.String, nullable=False)
    op.drop_column("users", "is_admin")
    op.drop_column("users", "groups")
