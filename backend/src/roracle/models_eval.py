"""Retrieval-quality evaluation: gold sets, runs and per-pair results (ADR-0015)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from roracle.models import TZ, Base


class EvalSet(Base):
    __tablename__ = "eval_sets"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 80", name="ck_eval_sets_name"),
        CheckConstraint("char_length(description) <= 2000", name="ck_eval_sets_description"),
        Index(
            "uq_eval_sets_one_reference", "is_reference", unique=True, postgresql_where=text("is_reference")
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    is_reference: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), onupdate=func.now())


class EvalQuery(Base):
    __tablename__ = "eval_queries"
    __table_args__ = (
        UniqueConstraint("set_id", "key", name="uq_eval_queries_key"),
        CheckConstraint("kind IN ('fact','multihop','global')", name="ck_eval_queries_kind"),
        CheckConstraint("status IN ('draft','active')", name="ck_eval_queries_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("eval_sets.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String)
    principals: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    as_pat: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    relevant: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    reference_answer: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (
        CheckConstraint(
            "trigger IN ('cli','ci','admin','model_change','reindex','compare')", name="ck_eval_runs_trigger"
        ),
        CheckConstraint("path IN ('chat','search')", name="ck_eval_runs_path"),
        Index("ix_eval_runs_set_started", "set_id", "started_at"),
        CheckConstraint("status IN ('running','done','failed')", name="ck_eval_runs_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    set_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("eval_sets.id", ondelete="SET NULL"))
    set_name: Mapped[str] = mapped_column(String)
    trigger: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String, default="", server_default="")
    path: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="running", server_default="running")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(TZ)


class EvalResult(Base):
    __tablename__ = "eval_results"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="CASCADE"), primary_key=True
    )
    query_key: Mapped[str] = mapped_column(String, primary_key=True)
    principal: Mapped[str] = mapped_column(String, primary_key=True)  # user id, or "<id>:pat"
    kind: Mapped[str] = mapped_column(String)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    ranked: Mapped[list[Any]] = mapped_column(JSONB)  # top-100 [[chunk_id, doc_id]], ids only
    judge: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    latency_ms: Mapped[int] = mapped_column(Integer)
