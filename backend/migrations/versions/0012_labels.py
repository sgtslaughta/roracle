"""sensitivity labels: labels table, documents/annotations/connectors.label_id, proposals 'label' kind

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def _gate(name: str) -> sa.Column:
    return sa.Column(name, sa.Boolean, nullable=False, server_default=sa.false())


def upgrade() -> None:
    op.create_table(
        "labels",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("colour", sa.String(7), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("rank", sa.Integer, nullable=False),
        _gate("exclude_llm"),
        _gate("block_agents"),
        _gate("block_export"),
        _gate("reason_on_lower"),
        _gate("is_default"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("colour ~ '^#[0-9a-fA-F]{6}$'", name="ck_labels_colour"),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 40", name="ck_labels_name"),
        sa.UniqueConstraint("rank", name="uq_labels_rank", deferrable=True, initially="DEFERRED"),
    )
    op.create_index("uq_labels_name_lower", "labels", [sa.text("lower(name)")], unique=True)
    op.create_index(
        "uq_labels_one_default", "labels", ["is_default"], unique=True, postgresql_where=sa.text("is_default")
    )
    op.execute(
        "INSERT INTO labels (id, name, colour, description, rank, is_default) "
        "VALUES (gen_random_uuid(), 'General', '#496580', 'Default label', 0, true)"
    )
    for table, nullable in (("documents", False), ("annotations", True), ("connectors", True)):
        op.add_column(table, sa.Column("label_id", UUID, sa.ForeignKey("labels.id", ondelete="RESTRICT")))
        if not nullable:
            op.execute("UPDATE documents SET label_id = (SELECT id FROM labels WHERE is_default)")
            op.alter_column(table, "label_id", nullable=False)
    op.create_index("ix_documents_label_id", "documents", ["label_id"])
    op.drop_constraint("ck_proposals_kind", "proposals")
    op.create_check_constraint(
        "ck_proposals_kind", "proposals", "kind IN ('note','relation','evidence','label')"
    )
    # ES lacks label_id until acl_sync converges; the Postgres post-filter is the authority meanwhile.
    op.execute(
        "INSERT INTO jobs (kind, payload) SELECT 'acl_sync', jsonb_build_object('doc_id', id::text) "
        "FROM documents WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DELETE FROM proposals WHERE kind = 'label'")
    op.drop_constraint("ck_proposals_kind", "proposals")
    op.create_check_constraint("ck_proposals_kind", "proposals", "kind IN ('note','relation','evidence')")
    op.drop_index("ix_documents_label_id", "documents")
    for table in ("connectors", "annotations", "documents"):
        op.drop_column(table, "label_id")
    op.drop_table("labels")
