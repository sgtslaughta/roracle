"""onboarding_progress: per-user Get started checklist state

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "onboarding_progress",
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("steps", ARRAY(sa.Text), server_default="{}", nullable=False),
        sa.Column("dismissed", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("onboarding_progress")
