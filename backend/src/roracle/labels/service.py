"""Document labelling: set/raise/lower rules, defaults for new documents, audit + notification."""

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from roracle import notify
from roracle.audit import audit
from roracle.auth import Principal
from roracle.labels.policy import LabelInfo, Policy
from roracle.models import Annotation, Document


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
    if chosen.rank < default.rank and not (p and p.is_admin and p.via == "jwt"):
        who = "agents cannot" if p and p.via != "jwt" else "only admins can"
        raise HTTPException(422, f"label is below the default; {who} choose it")
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


def note_label_for_create(
    session: Session, doc: Document, label_id: str | uuid.UUID | None
) -> uuid.UUID | None:
    """Label stored on a new note: null inherits; an explicit one may not rank below the document's."""
    if label_id is None:
        return None
    policy = Policy.load(session)
    new = resolve(policy, label_id)
    if new.rank < policy.get(doc.label_id).rank:
        raise HTTPException(422, "a note label cannot be below the document label")
    return uuid.UUID(new.id)


def set_note_label(
    session: Session,
    p: Principal,
    note: Annotation,
    label_id: str | uuid.UUID | None,
    reason: str | None,
    *,
    via: str = "user",
) -> LabelInfo:
    """Set a thread root's own label (null = inherit). Compared on EFFECTIVE labels (max(doc, note)):
    raise/equal -> author or admin; lower -> admin only (+ reason rule on the current effective label).
    A label below the document's -> 422. Returns the new effective label. Caller proved the note readable."""
    if p.via != "jwt":
        raise HTTPException(403, "agents must propose")
    if not (p.is_admin or note.author_id == p.user_id):
        raise HTTPException(403, "only the note author can change the label")
    if note.parent_id is not None:
        raise HTTPException(422, "labels apply to thread roots, not replies")
    session.refresh(note, with_for_update=True)
    doc = session.get(Document, note.document_id)
    policy = Policy.load(session)
    if label_id is not None and resolve(policy, label_id).rank < policy.get(doc.label_id).rank:
        raise HTTPException(422, "a note label cannot be below the document label")
    old = policy.effective(doc.label_id, note.label_id)
    new = policy.effective(doc.label_id, label_id)
    reason = (reason or "").strip() or None
    if reason and len(reason) > 500:
        raise HTTPException(422, "reason too long")
    old_own = policy.get(note.label_id) if note.label_id else None
    new_own = policy.get(label_id) if label_id is not None else None
    # own-label decrease (incl. clearing) is a lowering even when the doc label masks it
    own_lower = old_own is not None and (new_own is None or new_own.rank < old_own.rank)
    if new.rank < old.rank or own_lower:
        if not p.is_admin:
            raise HTTPException(403, "only admins can lower a label")
        needs = old.reason_on_lower or (own_lower and old_own.reason_on_lower)
        if needs and not (reason and 3 <= len(reason) <= 500):
            raise HTTPException(422, "a reason (3-500 characters) is required to lower this label")
    own_changed = (old_own.id if old_own else None) != (new_own.id if new_own else None)
    note.label_id = uuid.UUID(str(label_id)) if label_id is not None else None
    session.flush()
    if own_changed or new.id != old.id:
        detail = {
            "from": old.id,
            "to": new.id,
            "own_from": old_own.id if old_own else None,
            "own_to": new_own.id if new_own else None,
            "via": via,
            **({"reason": reason} if reason else {}),
        }
        audit(session, p, "annotation.label", str(note.id), detail)
    if new.id != old.id and p.user_id != note.author_id:
        notify.emit(
            session,
            note.author_id,
            "label.changed",
            {
                "document_id": str(doc.id),
                "annotation_id": str(note.id),
                "from": old.id,
                "to": new.id,
                "by": str(p.user_id),
            },
        )
    return new


def label_by_name(session: Session, name: str):
    """Case-insensitive name lookup; 422 listing the valid names when unknown (agent-facing)."""
    policy = Policy.load(session)
    for x in policy.labels:
        if x.name.lower() == name.strip().lower():
            return x
    raise HTTPException(422, f"unknown label; valid labels: {', '.join(x.name for x in policy.labels)}")
