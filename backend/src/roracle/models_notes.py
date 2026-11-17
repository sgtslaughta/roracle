"""Community notes tables (split from models.py for the file-size limit)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from roracle.models import TZ, Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Annotation(Base):
    __tablename__ = "annotations"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('comment','finding','correction','question','relation')", name="ck_annotations_kind"
        ),
        CheckConstraint(
            "relation_type IS NULL OR relation_type IN ('extends','contradicts','replicates','supersedes')",
            name="ck_annotations_relation_type",
        ),
        CheckConstraint("visibility IN ('private','shared','everyone')", name="ck_annotations_visibility"),
        CheckConstraint("status IN ('visible','hidden','removed')", name="ck_annotations_status"),
        CheckConstraint(
            "(kind = 'relation') = (relation_type IS NOT NULL AND target_document_id IS NOT NULL)",
            name="ck_annotations_relation",
        ),
        Index("ix_annotations_document_id_created_at", "document_id", "created_at"),
        Index("ix_annotations_parent_id", "parent_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("annotations.id", ondelete="CASCADE"))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    anchor: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    relation_type: Mapped[str | None] = mapped_column(String)
    target_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE")
    )
    visibility: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="visible", server_default="visible")
    agent_assisted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    stale_version: Mapped[int | None] = mapped_column(Integer)
    label_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("labels.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), onupdate=func.now())


class AnnotationShare(Base):
    __tablename__ = "annotation_shares"
    __table_args__ = (
        CheckConstraint("principal_kind IN ('user','group')", name="ck_annotation_shares_kind"),
    )

    annotation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("annotations.id", ondelete="CASCADE"), primary_key=True
    )
    principal_kind: Mapped[str] = mapped_column(String, primary_key=True)
    principal: Mapped[str] = mapped_column(String, primary_key=True)


class AnnotationVote(Base):
    __tablename__ = "annotation_votes"
    __table_args__ = (CheckConstraint("value IN (-1,1)", name="ck_annotation_votes_value"),)

    annotation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("annotations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    value: Mapped[int] = mapped_column(Integer)


class AnnotationReport(Base):
    __tablename__ = "annotation_reports"
    __table_args__ = (
        CheckConstraint("reason IN ('spam','abusive','wrong','other')", name="ck_annotation_reports_reason"),
    )

    annotation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("annotations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    reason: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint("kind IN ('note','relation','evidence','label')", name="ck_proposals_kind"),
        CheckConstraint("status IN ('pending','approved','rejected')", name="ck_proposals_status"),
        Index("ix_proposals_user_id_status_created_at", "user_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    client: Mapped[str] = mapped_column(String, default="", server_default="")
    kind: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String, default="pending", server_default="pending")
    annotation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("annotations.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(TZ)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint("stance IN ('supports','refutes','inconclusive')", name="ck_evidence_stance"),
        CheckConstraint(
            "scan_status IN ('none','pending','clean','infected','failed','unscanned')",
            name="ck_evidence_scan_status",
        ),
        CheckConstraint("NOT (blob_sha256 IS NOT NULL AND uri IS NOT NULL)", name="ck_evidence_file_xor_uri"),
        Index("ix_evidence_annotation_id", "annotation_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    annotation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("annotations.id", ondelete="CASCADE"))
    creator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    stance: Mapped[str] = mapped_column(String)
    crate: Mapped[dict[str, Any]] = mapped_column(JSONB)
    blob_sha256: Mapped[str | None] = mapped_column(ForeignKey("blobs.sha256"))
    filename: Mapped[str] = mapped_column(String, default="", server_default="")
    size: Mapped[int | None] = mapped_column(BigInteger)
    uri: Mapped[str | None] = mapped_column(String)
    uri_sha256: Mapped[str | None] = mapped_column(String(64))
    scan_status: Mapped[str] = mapped_column(String, default="none", server_default="none")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class EvidenceReproduction(Base):
    __tablename__ = "evidence_reproductions"
    __table_args__ = (
        CheckConstraint("outcome IN ('reproduced','failed')", name="ck_evidence_reproductions_outcome"),
    )

    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    outcome: Mapped[str] = mapped_column(String)
    note: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
