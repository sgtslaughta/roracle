"""p8 notes and evidence

Revision ID: 0008_p8
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_p8"
down_revision = "0007"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
TZ = sa.DateTime(timezone=True)


def _fk(col: str, target: str, ondelete: str | None = "CASCADE", **kw) -> sa.Column:
    return sa.Column(
        col,
        UUID if target != "blobs.sha256" else sa.String(64),
        sa.ForeignKey(target, ondelete=ondelete),
        **kw,
    )


def _now(name: str = "created_at") -> sa.Column:
    return sa.Column(name, TZ, nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "annotations",
        sa.Column("id", UUID, primary_key=True),
        _fk("document_id", "documents.id", nullable=False),
        _fk("version_id", "document_versions.id", nullable=False),
        _fk("parent_id", "annotations.id"),
        _fk("author_id", "users.id", nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("anchor", JSONB),
        sa.Column("relation_type", sa.String),
        _fk("target_document_id", "documents.id"),
        sa.Column("visibility", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="visible"),
        sa.Column("agent_assisted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("stale_version", sa.Integer),
        _now(),
        _now("updated_at"),
        sa.CheckConstraint(
            "kind IN ('comment','finding','correction','question','relation')", name="ck_annotations_kind"
        ),
        sa.CheckConstraint(
            "relation_type IS NULL OR relation_type IN ('extends','contradicts','replicates','supersedes')",
            name="ck_annotations_relation_type",
        ),
        sa.CheckConstraint("visibility IN ('private','shared','everyone')", name="ck_annotations_visibility"),
        sa.CheckConstraint("status IN ('visible','hidden','removed')", name="ck_annotations_status"),
        sa.CheckConstraint(
            "(kind = 'relation') = (relation_type IS NOT NULL AND target_document_id IS NOT NULL)",
            name="ck_annotations_relation",
        ),
    )
    op.create_index("ix_annotations_document_id_created_at", "annotations", ["document_id", "created_at"])
    op.create_index("ix_annotations_parent_id", "annotations", ["parent_id"])
    op.create_table(
        "annotation_shares",
        _fk("annotation_id", "annotations.id", primary_key=True),
        sa.Column("principal_kind", sa.String, primary_key=True),
        sa.Column("principal", sa.String, primary_key=True),
        sa.CheckConstraint("principal_kind IN ('user','group')", name="ck_annotation_shares_kind"),
    )
    op.create_table(
        "annotation_votes",
        _fk("annotation_id", "annotations.id", primary_key=True),
        _fk("user_id", "users.id", primary_key=True),
        sa.Column("value", sa.Integer, nullable=False),
        sa.CheckConstraint("value IN (-1,1)", name="ck_annotation_votes_value"),
    )
    op.create_table(
        "annotation_reports",
        _fk("annotation_id", "annotations.id", primary_key=True),
        _fk("user_id", "users.id", primary_key=True),
        sa.Column("reason", sa.String, nullable=False),
        _now(),
        sa.CheckConstraint(
            "reason IN ('spam','abusive','wrong','other')", name="ck_annotation_reports_reason"
        ),
    )
    op.create_table(
        "proposals",
        sa.Column("id", UUID, primary_key=True),
        _fk("user_id", "users.id", nullable=False),
        sa.Column("client", sa.String, nullable=False, server_default=""),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        _fk("annotation_id", "annotations.id", "SET NULL"),
        _now(),
        sa.Column("decided_at", TZ),
        sa.CheckConstraint("kind IN ('note','relation','evidence')", name="ck_proposals_kind"),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_proposals_status"),
    )
    op.create_index(
        "ix_proposals_user_id_status_created_at", "proposals", ["user_id", "status", "created_at"]
    )
    op.create_table(
        "evidence",
        sa.Column("id", UUID, primary_key=True),
        _fk("annotation_id", "annotations.id", nullable=False),
        _fk("creator_id", "users.id", nullable=False),
        sa.Column("stance", sa.String, nullable=False),
        sa.Column("crate", JSONB, nullable=False),
        _fk("blob_sha256", "blobs.sha256", None),
        sa.Column("filename", sa.String, nullable=False, server_default=""),
        sa.Column("size", sa.BigInteger),
        sa.Column("uri", sa.String),
        sa.Column("uri_sha256", sa.String(64)),
        sa.Column("scan_status", sa.String, nullable=False, server_default="none"),
        _now(),
        sa.CheckConstraint("stance IN ('supports','refutes','inconclusive')", name="ck_evidence_stance"),
        sa.CheckConstraint(
            "scan_status IN ('none','pending','clean','infected','failed','unscanned')",
            name="ck_evidence_scan_status",
        ),
        sa.CheckConstraint(
            "NOT (blob_sha256 IS NOT NULL AND uri IS NOT NULL)", name="ck_evidence_file_xor_uri"
        ),
    )
    op.create_index("ix_evidence_annotation_id", "evidence", ["annotation_id"])
    op.create_table(
        "evidence_reproductions",
        _fk("evidence_id", "evidence.id", primary_key=True),
        _fk("user_id", "users.id", primary_key=True),
        sa.Column("outcome", sa.String, nullable=False),
        sa.Column("note", sa.String(2000), nullable=False, server_default=""),
        _now(),
        sa.CheckConstraint("outcome IN ('reproduced','failed')", name="ck_evidence_reproductions_outcome"),
    )


def downgrade() -> None:
    for t in (
        "evidence_reproductions",
        "evidence",
        "proposals",
        "annotation_reports",
        "annotation_votes",
        "annotation_shares",
        "annotations",
    ):
        op.drop_table(t)
