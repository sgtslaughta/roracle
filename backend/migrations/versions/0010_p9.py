"""p9 paper writing: drafts, draft_versions, writing_profiles

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "drafts",
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("profile", sa.String),
        sa.Column("status", sa.String, nullable=False, server_default="draft"),
        sa.Column("coauthors", JSONB, nullable=False, server_default='{"users": [], "groups": []}'),
        sa.Column("ai_disclosure", JSONB),
        sa.Column("acks", JSONB, nullable=False, server_default="[]"),
        sa.Column("target", JSONB),
        sa.Column("submitted_version", sa.Integer),
        sa.Column("review_note", sa.Text),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('draft','in_review','submitted')", name="ck_drafts_status"),
    )
    op.create_table(
        "draft_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "document_id", UUID, sa.ForeignKey("drafts.document_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("author_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("validation", JSONB),
        sa.Column("citations", JSONB),
        sa.UniqueConstraint("document_id", "version", name="uq_draft_versions_doc_version"),
    )
    op.create_table(
        "writing_profiles",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("yaml", sa.Text, nullable=False),
        sa.Column("spec", JSONB, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id")),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name", "version", name="uq_writing_profiles_name_version"),
    )


def downgrade() -> None:
    op.drop_table("writing_profiles")
    op.drop_table("draft_versions")
    op.drop_table("drafts")
