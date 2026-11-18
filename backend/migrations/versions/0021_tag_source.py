"""tags.source (`user` | `llm` auto-tag), documents.rejected_tags + `auto_tags` becomes a bulk job kind

Downgrade: drain (or delete) queued/running `auto_tags` jobs first; the old claim indexes treat them as user work.

The claim indexes are recreated with the wider predicate exactly as 0019 (temporary name, validate, drop old +
rename; CONCURRENTLY). Drain workers first.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

OLD = "'rebuild_units', 'reconcile_tags', 'reparse_refs'"
NEW = "'rebuild_units', 'reconcile_tags', 'reparse_refs', 'auto_tags'"


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
    op.add_column("tags", sa.Column("source", sa.String(8), nullable=False, server_default="user"))
    op.create_check_constraint("ck_tags_source", "tags", "source IN ('user', 'llm')")
    op.add_column(  # AI tags the owner removed: never suggested again for this document
        "documents", sa.Column("rejected_tags", ARRAY(sa.String(40)), nullable=False, server_default="{}")
    )
    _both(NEW)


def downgrade() -> None:
    _both(OLD)
    op.drop_column("documents", "rejected_tags")
    op.drop_column("tags", "source")
