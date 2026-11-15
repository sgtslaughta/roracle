"""mcp handoff

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
TZ = sa.DateTime(timezone=True)


def _jsonb(name: str, default: str) -> sa.Column:
    return sa.Column(name, JSONB, nullable=False, server_default=default)


def upgrade() -> None:
    op.add_column("users", sa.Column("last_login_at", TZ))
    op.create_table(
        "personal_access_tokens",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("prefix", sa.String(12), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        _jsonb("scopes", '["read"]'),
        _jsonb("groups", "[]"),
        sa.Column("expires_at", TZ, nullable=False),
        sa.Column("revoked_at", TZ),
        sa.Column("last_used_at", TZ),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_personal_access_tokens_user_id", "personal_access_tokens", ["user_id"])
    op.create_table(
        "handoffs",
        sa.Column("code", sa.String(8), primary_key=True),
        sa.Column("creator_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False, server_default=""),
        sa.Column("goal", sa.Text, nullable=False, server_default=""),
        sa.Column("brief", sa.Text, nullable=False, server_default=""),
        _jsonb("source", "{}"),
        _jsonb("doc_ids", "[]"),
        _jsonb("passages", "[]"),
        _jsonb("transcript", "[]"),
        _jsonb("notes", "[]"),
        sa.Column("audience", sa.String, nullable=False, server_default="creator"),
        sa.Column("created_via", sa.String, nullable=False, server_default="ui"),
        sa.Column("expires_at", TZ, nullable=False),
        sa.Column("revoked_at", TZ),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "kind IN ('chat','collection','search','document','agent')", name="ck_handoffs_kind"
        ),
        sa.CheckConstraint("audience IN ('creator','shared','authenticated')", name="ck_handoffs_audience"),
        sa.CheckConstraint("created_via IN ('ui','mcp')", name="ck_handoffs_created_via"),
    )
    op.create_index("ix_handoffs_creator_id_created_at", "handoffs", ["creator_id", "created_at"])
    op.create_table(
        "handoff_shares",
        sa.Column(
            "handoff_code", sa.String(8), sa.ForeignKey("handoffs.code", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("principal_kind", sa.String, primary_key=True),
        sa.Column("principal", sa.String, primary_key=True),
        sa.CheckConstraint("principal_kind IN ('user','group')", name="ck_handoff_shares_kind"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("target", sa.String, nullable=False, server_default=""),
        sa.Column("client", sa.String, nullable=False, server_default=""),
        _jsonb("detail", "{}"),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"])


def downgrade() -> None:
    op.drop_column("users", "last_login_at")
    op.drop_table("audit_log")
    op.drop_table("handoff_shares")
    op.drop_table("handoffs")
    op.drop_table("personal_access_tokens")
