"""saved_searches: per-user saved library searches (URL query string)

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0020"
down_revision = "0019"  # main's 0019_reparse_refs_bulk; keep exactly one head
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_searches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "name", name="uq_saved_searches_owner_name"),
    )
    op.create_index("ix_saved_searches_owner_id", "saved_searches", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_saved_searches_owner_id", table_name="saved_searches")
    op.drop_table("saved_searches")
