"""hot-path indexes: browse, tag filter, shares lookup, org visibility, label.view audit

Same CONCURRENTLY / drop-invalid-first / verify-valid handling as 0014 (drain workers first: CIC waits for
open transactions).

Revision ID: 0014c
Revises: 0014b
"""

import sqlalchemy as sa
from alembic import op

revision = "0014c"
down_revision = "0014b"
branch_labels = None
depends_on = None

INDEXES = {
    "ix_versions_latest_searchable": (
        "document_versions (created_at DESC, document_id) WHERE is_latest AND stage = 'searchable'"
    ),
    "ix_tags_tag": "tags (tag, document_id)",
    "ix_document_shares_principal": "document_shares (principal_kind, principal, document_id)",
    "ix_documents_vis_org": "documents (visibility, org_unit) WHERE deleted_at IS NULL",
    "ix_audit_label_view": "audit_log (user_id, target, at) WHERE action = 'label.view'",
}


def _state(name: str, valid: bool) -> bool:
    q = "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n AND "
    q += "i.indisvalid" if valid else "NOT i.indisvalid"
    return bool(op.get_bind().scalar(sa.text(q), {"n": name}))


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, definition in INDEXES.items():
            if _state(
                name, valid=False
            ):  # a failed CIC leaves an INVALID index that IF NOT EXISTS would keep
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {definition}")
            if not _state(name, valid=True):
                raise RuntimeError(f"index {name} is not valid after creation; fix and re-run the migration")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in reversed(INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
