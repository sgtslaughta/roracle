"""jobs: aggressive autovacuum (a hot, update-heavy queue table: claim, heartbeat, complete)

Revision ID: 0014d
Revises: 0014c
"""

from alembic import op

revision = "0014d"
down_revision = "0014c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE jobs SET (autovacuum_vacuum_scale_factor = 0.01, autovacuum_analyze_scale_factor = 0.005)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE jobs RESET (autovacuum_vacuum_scale_factor, autovacuum_analyze_scale_factor)")
