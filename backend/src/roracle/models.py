import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
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
    last_login_at: Mapped[datetime | None] = mapped_column(TZ)


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
        Index("ix_documents_vis_org", "visibility", "org_unit", postgresql_where=text("deleted_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str | None] = mapped_column(String)
    org_unit: Mapped[str] = mapped_column(String)
    visibility: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(TZ)
    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connectors.id", ondelete="SET NULL"), index=True
    )
    label_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("labels.id", ondelete="RESTRICT"),
        index=True,
        default=text("(SELECT id FROM labels WHERE is_default)"),
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_versions_doc_version"),
        CheckConstraint(
            "stage IN ('uploaded','scanned','parsed','summarized','deduped','chunked','embedded',"
            "'searchable','review','needs_title','rejected','cancelled')",
            name="ck_document_versions_stage",
        ),
        Index(  # browse: newest searchable latest versions straight off the index, no sort
            "ix_versions_latest_searchable",
            text("created_at DESC"),
            "document_id",
            postgresql_where=text("is_latest AND stage = 'searchable'"),
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
    summary_embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    near_dup_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_versions.id"))
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, server_default="false")
    ingest_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class DocumentShare(Base):
    __tablename__ = "document_shares"
    __table_args__ = (
        CheckConstraint("principal_kind IN ('user','group')", name="ck_document_shares_kind"),
        Index("ix_document_shares_principal", "principal_kind", "principal", "document_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    principal_kind: Mapped[str] = mapped_column(String, primary_key=True)
    principal: Mapped[str] = mapped_column(String, primary_key=True)


class DocumentCoauthor(Base):
    """Published-paper co-author: read access regardless of visibility (written at draft publish)."""

    __tablename__ = "document_coauthors"
    __table_args__ = (Index("ix_document_coauthors_user_id", "user_id"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)


# Kinds claimed only when no user job is due. Single source for the claim SQL (queue.py) and the partial
# claim indexes below; adding one needs a migration recreating ix_jobs_due_user/ix_jobs_due_bulk
# (tests/test_queue_claim.py fails until the DB index predicates cover every bulk kind).
BULK_KINDS = ("rebuild_units", "reconcile_tags", "reparse_refs")


def bulk_sql(*, bulk: bool) -> str:
    quoted = ", ".join(f"'{k}'" for k in BULK_KINDS)
    if len(BULK_KINDS) == 1:
        return f"kind {'=' if bulk else '<>'} {quoted}"
    return f"kind {'IN' if bulk else 'NOT IN'} ({quoted})"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','done','failed')", name="ck_jobs_status"),
        # the claim repeats these predicates literally (queue.claim); bulk kinds get their own index so a
        # backlog of them never sits between a user job and the head of ix_jobs_due_user
        Index(
            "ix_jobs_due_user",
            "run_after",
            "id",
            postgresql_where=text(f"status = 'queued' AND {bulk_sql(bulk=False)}"),
        ),
        Index(
            "ix_jobs_due_bulk",
            "run_after",
            "id",
            postgresql_where=text(f"status = 'queued' AND {bulk_sql(bulk=True)}"),
        ),
        Index(  # claimed before a rebuild_units backlog: its own partial index, no scan past that backlog
            "ix_jobs_due_reconcile",
            "run_after",
            "id",
            postgresql_where=text("status = 'queued' AND kind = 'reconcile_tags'"),
        ),
        Index("ix_jobs_kind_status", "kind", "status"),
        Index("ix_jobs_lease", "lease_until", postgresql_where=text("status = 'running'")),  # requeue_expired
        Index("ix_jobs_version_id", text("(payload->>'version_id')"), "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String, default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    run_after: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    lease_until: Mapped[datetime | None] = mapped_column(
        TZ
    )  # while running: extended by the worker heartbeat
    claim_token: Mapped[int | None] = mapped_column(
        BigInteger
    )  # unique per claim (sequence): the fencing token
    released: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # shutdown/defer refunds
    last_error: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), onupdate=func.now())


class VersionLsh(Base):
    """LSH bands of a version's MinHash (16 x 8 rows): near-duplicate candidates by equality, no scan."""

    __tablename__ = "version_lsh"

    band: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    hash: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (Index("ix_version_lsh_version_id", "version_id"),)


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        CheckConstraint("value IN (-1, 1)", name="ck_votes_value"),
        CheckConstraint(
            "reason IS NULL OR reason IN ('outdated','wrong','low_quality','superseded')",
            name="ck_votes_reason",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    value: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (Index("ix_tags_tag", "tag", "document_id"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(40), primary_key=True)


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))


class CollectionItem(Base):
    __tablename__ = "collection_items"

    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collections.id"), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class DocCitation(Base):
    """Every library document a reference may denote (R18); the viewer's best visible one is picked at read time."""

    __tablename__ = "doc_citations"

    src_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), primary_key=True
    )
    ref_no: Mapped[int] = mapped_column(Integer, primary_key=True)  # 1-based position in version.references
    target_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    confidence: Mapped[float] = mapped_column(Float)


class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = (
        CheckConstraint("scope IN ('all','open') OR scope LIKE 'collection:%'", name="ck_chats_scope"),
        Index("ix_chats_owner_id_updated_at", "owner_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String, default="", server_default="")
    scope: Mapped[str] = mapped_column(String, default="all", server_default="all")
    model: Mapped[str | None] = mapped_column(String)
    pins: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    excludes: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), onupdate=func.now())
    hidden_at: Mapped[datetime | None] = mapped_column(TZ)  # owner removed it from their list (soft)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="ck_chat_messages_role"),
        CheckConstraint("status IN ('complete','interrupted','failed')", name="ck_chat_messages_status"),
        Index("ix_chat_messages_chat_id_created_at", "chat_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String, default="", server_default="")
    viewer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    citations: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    retrieved: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String, default="complete", server_default="complete")
    model: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class ChatShare(Base):
    __tablename__ = "chat_shares"
    __table_args__ = (CheckConstraint("principal_kind IN ('user','group')", name="ck_chat_shares_kind"),)

    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    principal_kind: Mapped[str] = mapped_column(String, primary_key=True)
    principal: Mapped[str] = mapped_column(String, primary_key=True)


class AnswerRating(Base):
    __tablename__ = "answer_ratings"
    __table_args__ = (CheckConstraint("value IN (-1, 1)", name="ck_answer_ratings_value"),)

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    value: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class PersonalAccessToken(Base):
    __tablename__ = "personal_access_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String)
    prefix: Mapped[str] = mapped_column(String(12))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[Any]] = mapped_column(JSONB, default=lambda: ["read"], server_default='["read"]')
    groups: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    expires_at: Mapped[datetime] = mapped_column(TZ)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
    last_used_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class Handoff(Base):
    __tablename__ = "handoffs"
    __table_args__ = (
        CheckConstraint("kind IN ('chat','collection','search','document','agent')", name="ck_handoffs_kind"),
        CheckConstraint("audience IN ('creator','shared','authenticated')", name="ck_handoffs_audience"),
        CheckConstraint("created_via IN ('ui','mcp')", name="ck_handoffs_created_via"),
        Index("ix_handoffs_creator_id_created_at", "creator_id", "created_at"),
    )

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    creator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String, default="", server_default="")
    goal: Mapped[str] = mapped_column(Text, default="", server_default="")
    brief: Mapped[str] = mapped_column(Text, default="", server_default="")
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    doc_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    passages: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    transcript: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    notes: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    audience: Mapped[str] = mapped_column(String, default="creator", server_default="creator")
    created_via: Mapped[str] = mapped_column(String, default="ui", server_default="ui")
    expires_at: Mapped[datetime] = mapped_column(TZ)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class HandoffShare(Base):
    __tablename__ = "handoff_shares"
    __table_args__ = (CheckConstraint("principal_kind IN ('user','group')", name="ck_handoff_shares_kind"),)

    handoff_code: Mapped[str] = mapped_column(
        ForeignKey("handoffs.code", ondelete="CASCADE"), primary_key=True
    )
    principal_kind: Mapped[str] = mapped_column(String, primary_key=True)
    principal: Mapped[str] = mapped_column(String, primary_key=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index(
            "ix_audit_label_view", "user_id", "target", "at", postgresql_where=text("action = 'label.view'")
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String)
    target: Mapped[str] = mapped_column(String, default="", server_default="")
    client: Mapped[str] = mapped_column(String, default="", server_default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class Connector(Base):
    __tablename__ = "connectors"
    __table_args__ = (
        CheckConstraint("kind IN ('s3','fs','http')", name="ck_connectors_kind"),
        CheckConstraint("visibility IN ('private','org_unit','everyone')", name="ck_connectors_visibility"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[str] = mapped_column(String)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    visibility: Mapped[str] = mapped_column(String)
    org_unit: Mapped[str] = mapped_column(String, default="", server_default="")
    schedule_minutes: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    label_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("labels.id", ondelete="RESTRICT"))
    next_run_at: Mapped[datetime | None] = mapped_column(TZ)
    last_run_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class ConnectorRun(Base):
    __tablename__ = "connector_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running','ok','partial','failed')", name="ck_connector_runs_status"),
        Index("ix_connector_runs_connector_id_id", "connector_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    log: Mapped[str] = mapped_column(Text, default="", server_default="")


class ConnectorItem(Base):
    __tablename__ = "connector_items"

    connector_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connectors.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String, primary_key=True)
    etag: Mapped[str] = mapped_column(String, default="", server_default="")
    sha256: Mapped[str] = mapped_column(String)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    seen_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_id_id", "user_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(TZ)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


from roracle import models_eval, models_labels, models_notes, models_saved, models_writing  # noqa: F401  (register tables)
from roracle.models_labels import Label  # noqa: F401
from roracle.models_notes import (  # noqa: F401
    Annotation,
    AnnotationReport,
    AnnotationShare,
    AnnotationVote,
    Evidence,
    EvidenceReproduction,
    Proposal,
)
