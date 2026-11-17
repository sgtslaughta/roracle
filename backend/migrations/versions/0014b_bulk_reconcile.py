"""jobs: reconcile_tags becomes a bulk kind (claim indexes recreated with the wider predicate)

Each index is built under a temporary name, validated, swapped in (drop old + rename), so the claim always has
an index. Same CONCURRENTLY / drop-invalid-first handling as 0014. Drain workers first (see 0014).

Revision ID: 0014b
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0014b"
down_revision = "0014"
branch_labels = None
depends_on = None


def _exists(name: str, valid: bool | None = None) -> bool:
    q = "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"
    if valid is not None:
        q += " AND i.indisvalid" if valid else " AND NOT i.indisvalid"
    return bool(op.get_bind().scalar(sa.text(q), {"n": name}))


def _swap(name: str, where: str) -> None:
    tmp = f"{name}_new"
    if _exists(tmp, valid=False):
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {tmp}")
    if not _exists(tmp):
        op.execute(f"CREATE INDEX CONCURRENTLY {tmp} ON jobs (run_after, id) WHERE {where}")
    if not _exists(tmp, valid=True):
        raise RuntimeError(f"index {tmp} is not valid after creation; fix and re-run the migration")
    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute(f"ALTER INDEX {tmp} RENAME TO {name}")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _swap("ix_jobs_due_user", "status = 'queued' AND kind NOT IN ('rebuild_units', 'reconcile_tags')")
        _swap("ix_jobs_due_bulk", "status = 'queued' AND kind IN ('rebuild_units', 'reconcile_tags')")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        _swap("ix_jobs_due_user", "status = 'queued' AND kind <> 'rebuild_units'")
        _swap("ix_jobs_due_bulk", "status = 'queued' AND kind = 'rebuild_units'")
