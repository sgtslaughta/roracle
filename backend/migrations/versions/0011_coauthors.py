"""document_coauthors: co-author read grant on published papers

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "document_coauthors",
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), primary_key=True),
    )
    op.create_index("ix_document_coauthors_user_id", "document_coauthors", ["user_id"])


def downgrade() -> None:
    op.drop_table("document_coauthors")
