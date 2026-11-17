"""jobs: split partial claim indexes (user vs bulk), (kind, status) for schedulers; drop the old one

Revision ID: 0014
Revises: 0013
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():  # CONCURRENTLY cannot run inside a transaction
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_due_user ON jobs (run_after, id)"
            " WHERE status = 'queued' AND kind <> 'rebuild_units'"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_due_bulk ON jobs (run_after, id)"
            " WHERE status = 'queued' AND kind = 'rebuild_units'"
        )
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_kind_status ON jobs (kind, status)")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_status_run_after")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_status_run_after ON jobs (status, run_after)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_kind_status")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_due_bulk")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_due_user")
