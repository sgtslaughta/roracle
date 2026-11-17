"""jobs: partial index for the reconcile_tags claim (claimed before a rebuild_units backlog)

CONCURRENTLY, drop-invalid-first, verify-valid, re-runnable (as 0014c; drain workers first: CIC waits for open
transactions).

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

NAME = "ix_jobs_due_reconcile"
DEFINITION = "jobs (run_after, id) WHERE status = 'queued' AND kind = 'reconcile_tags'"


def _state(valid: bool) -> bool:
    q = "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n AND "
    q += "i.indisvalid" if valid else "NOT i.indisvalid"
    return bool(op.get_bind().scalar(sa.text(q), {"n": NAME}))


def upgrade() -> None:
    with op.get_context().autocommit_block():
        if _state(valid=False):  # a failed CIC leaves an INVALID index that IF NOT EXISTS would keep
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {NAME}")
        op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {NAME} ON {DEFINITION}")
        if not _state(valid=True):
            raise RuntimeError(f"index {NAME} is not valid after creation; fix and re-run the migration")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {NAME}")
