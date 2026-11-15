"""document_versions.summary_embedding

Revision ID: 0006_p2b
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_p2b"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_versions", sa.Column("summary_embedding", sa.LargeBinary))


def downgrade() -> None:
    op.drop_column("document_versions", "summary_embedding")
