"""P9 writing tables (split from models.py for the file-size limit)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from roracle.models import TZ, Base


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = (
        CheckConstraint("status IN ('draft','in_review','submitted')", name="ck_drafts_status"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    profile: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="draft", server_default="draft")
    coauthors: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=lambda: {"users": [], "groups": []}, server_default='{"users": [], "groups": []}'
    )
    ai_disclosure: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    acks: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    target: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    submitted_version: Mapped[int | None] = mapped_column(Integer)
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class DraftVersion(Base):
    __tablename__ = "draft_versions"
    __table_args__ = (UniqueConstraint("document_id", "version", name="uq_draft_versions_doc_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drafts.document_id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    citations: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class WritingProfile(Base):
    __tablename__ = "writing_profiles"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_writing_profiles_name_version"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer)
    yaml: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
