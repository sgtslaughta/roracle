import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TZ = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sub: Mapped[str] = mapped_column(String, unique=True)
    username: Mapped[str] = mapped_column(String)
    email: Mapped[str | None] = mapped_column(String)
    groups: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class Blob(Base):
    __tablename__ = "blobs"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    refcount: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('private','shared','org_unit','everyone')", name="ck_documents_visibility"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str | None] = mapped_column(String)
    org_unit: Mapped[str] = mapped_column(String)
    visibility: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(TZ)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_versions_doc_version"),
        CheckConstraint(
            "stage IN ('uploaded','scanned','parsed','summarized','deduped','chunked','embedded',"
            "'searchable','review','needs_title','rejected','cancelled')",
            name="ck_document_versions_stage",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    version: Mapped[int] = mapped_column(Integer)
    blob_sha256: Mapped[str] = mapped_column(ForeignKey("blobs.sha256"), index=True)
    filename: Mapped[str] = mapped_column(String)
    content_type: Mapped[str] = mapped_column(String)
    size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    stage: Mapped[str] = mapped_column(String, default="uploaded", server_default="uploaded")
    page_count: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(String)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    references: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    minhash: Mapped[bytes | None] = mapped_column(LargeBinary)
    near_dup_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_versions.id"))
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    ingest_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class DocumentShare(Base):
    __tablename__ = "document_shares"
    __table_args__ = (CheckConstraint("principal_kind IN ('user','group')", name="ck_document_shares_kind"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    principal_kind: Mapped[str] = mapped_column(String, primary_key=True)
    principal: Mapped[str] = mapped_column(String, primary_key=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','done','failed')", name="ck_jobs_status"),
        Index("ix_jobs_status_run_after", "status", "run_after"),
        Index("ix_jobs_version_id", text("(payload->>'version_id')"), "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String, default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    run_after: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), onupdate=func.now())
