"""Immutable per-request label policy: rank ordering, gate sets and effective-label maths."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy import or_, select, text, true
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from roracle.audit import audit
from roracle.auth import Principal
from roracle.models import AuditLog, Document
from roracle.models_labels import GATES, Label


@dataclass(frozen=True)
class LabelInfo:
    id: str
    name: str
    colour: str
    description: str
    rank: int
    exclude_llm: bool
    block_agents: bool
    block_export: bool
    reason_on_lower: bool
    is_default: bool


def snapshot(row: Label) -> LabelInfo:
    return LabelInfo(
        id=str(row.id),
        name=row.name,
        colour=row.colour,
        description=row.description or "",
        rank=row.rank,
        exclude_llm=bool(row.exclude_llm),
        block_agents=bool(row.block_agents),
        block_export=bool(row.block_export),
        reason_on_lower=bool(row.reason_on_lower),
        is_default=bool(row.is_default),
    )


def label_json(label: LabelInfo) -> dict[str, Any]:
    return {
        "id": label.id,
        "name": label.name,
        "colour": label.colour,
        "rank": label.rank,
        "gates": {g: getattr(label, g) for g in GATES},
    }


def label_brief(label: LabelInfo) -> dict[str, Any]:
    """Agent-facing label: name and rank only (no colour, no gates)."""
    return {"name": label.name, "rank": label.rank}


def brief_labels(obj: Any) -> Any:
    """Recursively replace full label JSON (has `gates`) with `label_brief` shape, for MCP output."""
    if isinstance(obj, dict):
        if "gates" in obj and "rank" in obj and "name" in obj:
            return {"name": obj["name"], "rank": obj["rank"]}
        return {k: brief_labels(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [brief_labels(v) for v in obj]
    return obj


def admin_json(label: LabelInfo) -> dict[str, Any]:
    return {**label_json(label), "description": label.description, "is_default": label.is_default}


class Policy:
    def __init__(self, labels: list[LabelInfo]):
        self.labels: tuple[LabelInfo, ...] = tuple(sorted(labels, key=lambda x: x.rank))
        self._by_id = {x.id: x for x in self.labels}

    @classmethod
    def load(cls, session: Session) -> "Policy":
        return cls([snapshot(r) for r in session.scalars(select(Label))])

    def ids(self, gate: str) -> frozenset[str]:
        if gate not in GATES:
            raise ValueError(f"unknown gate {gate!r}")
        return frozenset(x.id for x in self.labels if getattr(x, gate))

    def get(self, label_id: str | uuid.UUID) -> LabelInfo:
        return self._by_id[str(label_id)]

    def has(self, label_id: str | uuid.UUID, gate: str) -> bool:
        return getattr(self.get(label_id), gate)

    def effective(self, doc_label_id: str | uuid.UUID, note_label_id: str | uuid.UUID | None) -> LabelInfo:
        doc = self.get(doc_label_id)
        if note_label_id is None:
            return doc
        note = self.get(note_label_id)
        return note if note.rank > doc.rank else doc

    @property
    def default(self) -> LabelInfo:
        return next(x for x in self.labels if x.is_default)


class ExportBlocked(Exception):
    def __init__(self, label: str):
        super().__init__(label)
        self.label = label


def export_blocked_handler(_request, exc: ExportBlocked) -> JSONResponse:
    return JSONResponse({"detail": "export blocked by label", "label": exc.label}, status_code=403)


# ---- gate (c): export / download ------------------------------------------------------------------
def export_exempt(p: Principal, doc: Document) -> bool:
    """R9: the owner holds the original and admins can relabel anyway."""
    return p.is_admin or doc.owner_id == p.user_id


def export_block(session: Session, p: Principal, doc: Document, note_label_id=None) -> LabelInfo | None:
    """The blocking label (effective = max(doc, note)), or None when `p` may export `doc`."""
    if export_exempt(p, doc):
        return None
    lab = Policy.load(session).effective(doc.label_id, note_label_id)
    return lab if lab.block_export else None


def require_exportable(session: Session, p: Principal, doc: Document, route: str, note_label_id=None) -> None:
    """403 (+ audit, committed so the 403 does not roll it back) when the label blocks export for `p`.

    Call only after normal visibility passed: invisible stays 404, so naming the label leaks nothing."""
    lab = export_block(session, p, doc, note_label_id)
    if lab is None:
        return
    audit(session, p, "label.export_blocked", str(doc.id), {"route": route})
    session.commit()
    raise ExportBlocked(lab.name)


def exportable_clause(p: Principal) -> ColumnElement[bool]:
    """SQL mirror of `export_block is None` for Document rows (bibtex silently omits the rest)."""
    if p.is_admin:
        return true()
    blocked = select(Label.id).where(Label.block_export.is_(True))
    return or_(Document.owner_id == p.user_id, Document.label_id.not_in(blocked))


def label_view_seen_stmt(user_id, target: str, since: datetime):
    return (
        select(AuditLog.id)
        .where(
            AuditLog.user_id == user_id,
            text("audit_log.action = 'label.view'"),  # literal: matches the partial index ix_audit_label_view
            AuditLog.target == target,
            AuditLog.at >= since,
        )
        .limit(1)
    )


def audit_inline_view(session: Session, p: Principal, doc: Document, route: str) -> None:
    """`label.view` audit row when a non-owner/non-admin reads a block_export doc inline (a deterrent,
    not DRM). One row per (user, document, UTC day) so reader paging cannot flood the log."""
    if export_block(session, p, doc) is None:
        return
    day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if session.scalar(label_view_seen_stmt(p.user_id, str(doc.id), day)) is None:
        audit(session, p, "label.view", str(doc.id), {"route": route})
