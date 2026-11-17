"""jobs.claim_token + jobs.released: a never-reused fencing token per claim, refunds tracked separately

`attempts` stays strictly increasing (it was the fence before; decrementing it on a shutdown release made the
token reusable). `claim_token` comes from a sequence; `released` counts refunds (shutdown release, deferral) and the
retry budget is `attempts - released`. Re-runnable (IF NOT EXISTS).

Revision ID: 0016
Revises: 0015
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("CREATE SEQUENCE IF NOT EXISTS jobs_claim_seq")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS claim_token bigint")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS released integer NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS released")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS claim_token")
    op.execute("DROP SEQUENCE IF EXISTS jobs_claim_seq")
