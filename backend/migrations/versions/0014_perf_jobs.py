"""jobs: split partial claim indexes (user vs bulk), (kind, status) for schedulers; drop the old one

Upgrade note: CREATE INDEX CONCURRENTLY waits for every open transaction, and a claim, its handler and its
completion share one transaction. Drain workers (or let long jobs finish) before `roracle migrate`.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

NEW = {
    "ix_jobs_due_user": "(run_after, id) WHERE status = 'queued' AND kind <> 'rebuild_units'",
    "ix_jobs_due_bulk": "(run_after, id) WHERE status = 'queued' AND kind = 'rebuild_units'",
    "ix_jobs_kind_status": "(kind, status)",
}


def _invalid(name: str) -> bool:
    return bool(
        op.get_bind().scalar(
            sa.text(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid"
                " WHERE c.relname = :n AND NOT i.indisvalid"
            ),
            {"n": name},
        )
    )


def _valid(name: str) -> bool:
    return bool(
        op.get_bind().scalar(
            sa.text(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid"
                " WHERE c.relname = :n AND i.indisvalid"
            ),
            {"n": name},
        )
    )


def _create(name: str, definition: str) -> None:
    """A failed CIC leaves an INVALID index that IF NOT EXISTS would keep: drop it first, then verify."""
    if _invalid(name):
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON jobs {definition}")
    if not _valid(name):
        raise RuntimeError(f"index {name} is not valid after creation; fix and re-run the migration")


def upgrade() -> None:
    with op.get_context().autocommit_block():  # CONCURRENTLY cannot run inside a transaction
        for name, definition in NEW.items():
            _create(name, definition)
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_status_run_after"
        )  # only once the new ones are valid


def downgrade() -> None:
    with op.get_context().autocommit_block():
        _create("ix_jobs_status_run_after", "(status, run_after)")
        for name in reversed(NEW):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
