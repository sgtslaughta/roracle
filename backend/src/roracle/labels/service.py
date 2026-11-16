"""Document labelling: set/raise/lower rules, defaults for new documents, audit + notification."""

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from roracle import notify
from roracle.audit import audit
from roracle.auth import Principal
from roracle.labels.policy import LabelInfo, Policy
from roracle.models import Document


def resolve(policy: Policy, label_id: str | uuid.UUID) -> LabelInfo:
    try:
        return policy.get(label_id)
    except KeyError:
        raise HTTPException(422, "unknown label") from None


def _record(
    session: Session, p: Principal, doc: Document, old: LabelInfo, new: LabelInfo, reason, via
) -> None:
    detail = {"from": old.id, "to": new.id, "via": via, **({"reason": reason} if reason else {})}
    audit(session, p, "document.label", str(doc.id), detail)
    if p.user_id != doc.owner_id:
        notify.emit(
            session,
            doc.owner_id,
            "label.changed",
            {"document_id": str(doc.id), "from": old.id, "to": new.id, "by": str(p.user_id)},
        )


def set_document_label(
    session: Session,
    p: Principal,
    doc: Document,
    label_id: str | uuid.UUID,
    reason: str | None,
    *,
    via: str = "user",
) -> LabelInfo:
    """Raise/keep: owner or admin. Lower: admin only, with a 3-500 char reason when the current label
    demands one. Caller has already proven `doc` visible (404) and triggers the ES acl sync."""
    if p.via != "jwt":
        raise HTTPException(403, "agents must propose")
    if not (p.is_admin or doc.owner_id == p.user_id):
        raise HTTPException(403, "only the document owner can change the label")
    policy = Policy.load(session)
    new = resolve(policy, label_id)
    old = policy.get(doc.label_id)
    reason = (reason or "").strip() or None
    if new.rank == old.rank:
        return old
    if new.rank < old.rank:
        if not p.is_admin:
            raise HTTPException(403, "only admins can lower a label")
        if old.reason_on_lower and not (reason and 3 <= len(reason) <= 500):
            raise HTTPException(422, "a reason (3-500 characters) is required to lower this label")
    if reason and len(reason) > 500:
        raise HTTPException(422, "reason too long")
    doc.label_id = uuid.UUID(new.id)
    session.flush()
    _record(session, p, doc, old, new, reason, via)
    return new


def initial_label(
    session: Session,
    requested_id: str | uuid.UUID | None,
    *,
    p: Principal | None = None,
    connector=None,
) -> uuid.UUID:
    """Label for a new document (R4). Connector docs: connector label or global default. Uploads: the
    requested label must rank >= default unless the caller is an admin (choosing lower = lowering)."""
    policy = Policy.load(session)
    default = policy.default
    if connector is not None:
        return connector.label_id or uuid.UUID(default.id)
    if requested_id is None:
        return uuid.UUID(default.id)
    chosen = resolve(policy, requested_id)
    if chosen.rank < default.rank and not (p and p.is_admin):
        raise HTTPException(422, "label is below the default; only admins can choose it")
    return uuid.UUID(chosen.id)


def raise_document_label(
    session: Session, p: Principal, doc: Document, label_id: str | uuid.UUID | None, *, via: str
) -> bool:
    """Raise `doc` to max(current, label_id), never lowering. No permission check: callers already
    proved the right to add content (submit, near-dup). True when changed."""
    if label_id is None:
        return False
    policy = Policy.load(session)
    new, old = resolve(policy, label_id), policy.get(doc.label_id)
    if new.rank <= old.rank:
        return False
    doc.label_id = uuid.UUID(new.id)
    session.flush()
    _record(session, p, doc, old, new, None, via)
    return True
