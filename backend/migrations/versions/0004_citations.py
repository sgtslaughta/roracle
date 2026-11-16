"""doc_citations

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "doc_citations",
        sa.Column(
            "src_version_id",
            UUID,
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ref_no", sa.Integer, primary_key=True),
        sa.Column(
            "target_document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("confidence", sa.Float, nullable=False),
    )
    op.create_index("ix_doc_citations_target_document_id", "doc_citations", ["target_document_id"])


def downgrade() -> None:
    op.drop_table("doc_citations")
