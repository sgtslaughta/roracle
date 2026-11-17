"""jobs.lease_until: lease-based execution (ADR-0003 amendment, Task 10.3)

A claim now commits at once (status running + lease); a worker heartbeat extends the lease; an expired lease is
requeued by `queue.requeue_expired`. Same CONCURRENTLY / drop-invalid-first handling as 0014 for the index.
Upgrade: drain workers first (an old worker holds the claim in an open transaction; a new worker claims through
the lease). Rows left `running` by an old worker do not exist: the old claim never committed.

Revision ID: 0015
Revises: 0014e
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014e"
branch_labels = None
depends_on = None


def _invalid(name: str) -> bool:
    q = "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n AND NOT i.indisvalid"
    return bool(op.get_bind().scalar(sa.text(q), {"n": name}))


def upgrade() -> None:
    # re-runnable (the CONCURRENTLY step below commits the ADD COLUMN first); never queue behind an undrained worker
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease_until timestamptz")
    with op.get_context().autocommit_block():
        if _invalid("ix_jobs_lease"):
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_lease")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_lease ON jobs (lease_until) WHERE status = 'running'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_lease")
    op.drop_column("jobs", "lease_until")
