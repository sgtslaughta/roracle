"""Sensitivity labels (split from models.py for the file-size limit)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from roracle.models import TZ, Base

GATES = ("exclude_llm", "block_agents", "block_export", "reason_on_lower")
MAX_LABELS = 20


class Label(Base):
    __tablename__ = "labels"
    __table_args__ = (
        CheckConstraint("colour ~ '^#[0-9a-fA-F]{6}$'", name="ck_labels_colour"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 40", name="ck_labels_name"),
        UniqueConstraint("rank", name="uq_labels_rank", deferrable=True, initially="DEFERRED"),
        Index("uq_labels_name_lower", text("lower(name)"), unique=True),
        Index("uq_labels_one_default", "is_default", unique=True, postgresql_where=text("is_default")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(40))
    colour: Mapped[str] = mapped_column(String(7))
    description: Mapped[str] = mapped_column(String(500), default="", server_default="")
    rank: Mapped[int] = mapped_column(Integer)
    exclude_llm: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    block_agents: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    block_export: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    reason_on_lower: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
