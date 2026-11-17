"""retrieval-quality eval: gold sets, queries, runs, results

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "eval_sets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("is_reference", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 80", name="ck_eval_sets_name"),
        sa.CheckConstraint("char_length(description) <= 2000", name="ck_eval_sets_description"),
    )
    op.create_index(
        "uq_eval_sets_one_reference",
        "eval_sets",
        ["is_reference"],
        unique=True,
        postgresql_where=sa.text("is_reference"),
    )
    op.create_table(
        "eval_queries",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("set_id", UUID, sa.ForeignKey("eval_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("principals", postgresql.ARRAY(UUID), nullable=False),
        sa.Column("as_pat", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("relevant", postgresql.JSONB, nullable=False),
        sa.Column("reference_answer", sa.Text),
        sa.Column("status", sa.String, nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("set_id", "key", name="uq_eval_queries_key"),
        sa.CheckConstraint("kind IN ('fact','multihop','global')", name="ck_eval_queries_kind"),
        sa.CheckConstraint("status IN ('draft','active')", name="ck_eval_queries_status"),
    )
    op.create_table(
        "eval_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("set_id", UUID, sa.ForeignKey("eval_sets.id", ondelete="SET NULL")),
        sa.Column("set_name", sa.String, nullable=False),
        sa.Column("trigger", sa.String, nullable=False),
        sa.Column("label", sa.String, nullable=False, server_default=""),
        sa.Column("path", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="running"),
        sa.Column("config", postgresql.JSONB, nullable=False),
        sa.Column("metrics", postgresql.JSONB),
        sa.Column("error", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "trigger IN ('cli','ci','admin','model_change','reindex','compare')", name="ck_eval_runs_trigger"
        ),
        sa.CheckConstraint("path IN ('chat','search')", name="ck_eval_runs_path"),
        sa.CheckConstraint("status IN ('running','done','failed')", name="ck_eval_runs_status"),
    )
    op.create_index("ix_eval_runs_set_started", "eval_runs", ["set_id", "started_at"])
    op.create_table(
        "eval_results",
        sa.Column("run_id", UUID, sa.ForeignKey("eval_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("query_key", sa.String, primary_key=True),
        sa.Column("principal", sa.String, primary_key=True),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("metrics", postgresql.JSONB),
        sa.Column("ranked", postgresql.JSONB, nullable=False),
        sa.Column("judge", postgresql.JSONB),
        sa.Column("latency_ms", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    for t in ("eval_results", "eval_runs", "eval_queries", "eval_sets"):
        op.drop_table(t)
