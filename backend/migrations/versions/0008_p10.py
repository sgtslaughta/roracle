"""p10 connectors, notifications, app settings

Revision ID: 0008_p10
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_p10"
down_revision = "0007"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
TZ = sa.DateTime(timezone=True)


def _cid() -> sa.Column:
    return sa.Column("connector_id", UUID, sa.ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "connectors",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("visibility", sa.String, nullable=False),
        sa.Column("org_unit", sa.String, nullable=False, server_default=""),
        sa.Column("schedule_minutes", sa.Integer),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("next_run_at", TZ),
        sa.Column("last_run_at", TZ),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('s3','fs','http')", name="ck_connectors_kind"),
        sa.CheckConstraint(
            "visibility IN ('private','org_unit','everyone')", name="ck_connectors_visibility"
        ),
    )
    op.create_table(
        "connector_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _cid(),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("started_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", TZ),
        sa.Column("stats", JSONB, nullable=False, server_default="{}"),
        sa.Column("log", sa.Text, nullable=False, server_default=""),
        sa.CheckConstraint("status IN ('running','ok','partial','failed')", name="ck_connector_runs_status"),
    )
    op.create_index("ix_connector_runs_connector_id_id", "connector_runs", ["connector_id", "id"])
    op.create_table(
        "connector_items",
        _cid(),
        sa.Column("key", sa.String, nullable=False),
        sa.Column("etag", sa.String, nullable=False, server_default=""),
        sa.Column("sha256", sa.String, nullable=False),
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("seen_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("connector_id", "key"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("read_at", TZ),
    )
    op.create_index("ix_notifications_user_id_id", "notifications", ["user_id", "id"])
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String, primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", UUID),
    )
    op.add_column(
        "documents",
        sa.Column("connector_id", UUID, sa.ForeignKey("connectors.id", ondelete="SET NULL")),
    )
    op.create_index("ix_documents_connector_id", "documents", ["connector_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_connector_id", "documents")
    op.drop_column("documents", "connector_id")
    op.drop_table("app_settings")
    op.drop_table("notifications")
    op.drop_table("connector_items")
    op.drop_table("connector_runs")
    op.drop_table("connectors")
