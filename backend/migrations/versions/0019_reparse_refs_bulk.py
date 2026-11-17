"""jobs: reparse_refs becomes a bulk kind (claim indexes recreated with the wider predicate)

Same swap procedure as 0014b (temporary name, validate, drop old + rename; CONCURRENTLY). Drain workers first.

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

OLD = "'rebuild_units', 'reconcile_tags'"
NEW = "'rebuild_units', 'reconcile_tags', 'reparse_refs'"


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


def _both(kinds: str) -> None:
    with op.get_context().autocommit_block():
        _swap("ix_jobs_due_user", f"status = 'queued' AND kind NOT IN ({kinds})")
        _swap("ix_jobs_due_bulk", f"status = 'queued' AND kind IN ({kinds})")


def upgrade() -> None:
    _both(NEW)


def downgrade() -> None:
    _both(OLD)
