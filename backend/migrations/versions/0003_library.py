"""votes, tags, collections

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "votes",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("value", sa.Integer, nullable=False),
        sa.Column("reason", sa.String),
        sa.Column("superseded_by", UUID, sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("value IN (-1, 1)", name="ck_votes_value"),
        sa.CheckConstraint(
            "reason IS NULL OR reason IN ('outdated','wrong','low_quality','superseded')",
            name="ck_votes_reason",
        ),
    )
    op.create_index("ix_votes_document_id", "votes", ["document_id"])
    op.create_index("ix_votes_superseded_by", "votes", ["superseded_by"])
    op.create_table(
        "tags",
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag", sa.String(40), primary_key=True),
    )
    op.create_table(
        "collections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
    )
    op.create_index("ix_collections_owner_id", "collections", ["owner_id"])
    op.create_table(
        "collection_items",
        sa.Column("collection_id", UUID, sa.ForeignKey("collections.id"), primary_key=True),
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_collection_items_document_id", "collection_items", ["document_id"])


def downgrade() -> None:
    for t in ("collection_items", "collections", "tags", "votes"):
        op.drop_table(t)
