"""chats

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "chats",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String, nullable=False, server_default=""),
        sa.Column("scope", sa.String, nullable=False, server_default="all"),
        sa.Column("model", sa.String),
        sa.Column("pins", JSONB, nullable=False, server_default="[]"),
        sa.Column("excludes", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("scope IN ('all','open') OR scope LIKE 'collection:%'", name="ck_chats_scope"),
    )
    op.create_index("ix_chats_owner_id", "chats", ["owner_id"])
    op.create_index("ix_chats_owner_id_updated_at", "chats", ["owner_id", "updated_at"])
    op.create_table(
        "chat_messages",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("chat_id", UUID, sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("content", sa.String, nullable=False, server_default=""),
        sa.Column("viewer", JSONB),
        sa.Column("citations", JSONB, nullable=False, server_default="[]"),
        sa.Column("retrieved", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String, nullable=False, server_default="complete"),
        sa.Column("model", sa.String),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_chat_messages_role"),
        sa.CheckConstraint("status IN ('complete','interrupted','failed')", name="ck_chat_messages_status"),
    )
    op.create_index("ix_chat_messages_chat_id_created_at", "chat_messages", ["chat_id", "created_at"])
    op.create_table(
        "chat_shares",
        sa.Column("chat_id", UUID, sa.ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("principal_kind", sa.String, primary_key=True),
        sa.Column("principal", sa.String, primary_key=True),
        sa.CheckConstraint("principal_kind IN ('user','group')", name="ck_chat_shares_kind"),
    )
    op.create_table(
        "answer_ratings",
        sa.Column(
            "message_id", UUID, sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("value", sa.Integer, nullable=False),
        sa.Column("comment", sa.String),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("value IN (-1, 1)", name="ck_answer_ratings_value"),
    )


def downgrade() -> None:
    op.drop_table("answer_ratings")
    op.drop_table("chat_shares")
    op.drop_table("chat_messages")
    op.drop_table("chats")
