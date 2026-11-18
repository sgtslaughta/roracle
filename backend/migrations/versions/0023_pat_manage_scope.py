"""personal_access_tokens: backfill the `manage` scope onto every token that holds `write`

Revision ID: 0023
Revises: 0022
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

BACKFILL_SQL = (
    "UPDATE personal_access_tokens SET scopes = scopes || '[\"manage\"]'::jsonb "
    "WHERE scopes ? 'write' AND NOT scopes ? 'manage'"
)


def upgrade() -> None:
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.execute("UPDATE personal_access_tokens SET scopes = scopes - 'manage'")
