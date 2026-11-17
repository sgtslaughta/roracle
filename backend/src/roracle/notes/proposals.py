"""Agent proposals: validated like notes, stored only as a Proposal until a human approves (P8 task 4)."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from roracle.audit import audit
from roracle.auth import Principal
from roracle.labels import service as label_service
from roracle.labels.policy import Policy
from roracle.models import Annotation, Document, Proposal
from roracle.notes import service
from roracle.notes.access import doc_readable, get_readable
from roracle.notes.schemas import BODY_MAX, SHARES_MAX, NoteIn, ReplyIn, Share, Visibility
from roracle.notify import emit
from roracle.queue import enqueue

PENDING_MAX = 50
_META = ("document_id", "parent_id")


class ApproveIn(BaseModel):
    body: str | None = Field(None, min_length=1, max_length=BODY_MAX)
    visibility: Visibility | None = None
    shares: list[Share] | None = Field(None, max_length=SHARES_MAX)
    reason: str | None = Field(None, max_length=500)  # label proposals: lowering reason


def _422(e: ValidationError) -> HTTPException:
    return HTTPException(422, "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))


def propose(session: Session, p: Principal, kind: str, payload: dict) -> Proposal:
    """payload = note fields + `document_id` (+ `parent_id` for a reply). Visibility defaults to private."""
    if kind not in ("note", "relation", "label"):
        raise HTTPException(422, "proposal kind must be note, relation or label")
    try:
        if kind == "label":
            return _store(session, p, kind, _label_payload(session, p, payload))
        if payload.get("parent_id"):
            if kind != "note":
                raise HTTPException(422, "a reply is a note proposal")
            parent_id = UUID(str(payload["parent_id"]))
            parent = get_readable(session, p, parent_id)
            ReplyIn(body=payload.get("body"))
            stored = {
                "document_id": str(parent.document_id),
                "parent_id": str(parent_id),
                "body": payload["body"],
            }
        else:
            doc_id = UUID(str(payload["document_id"]))
            note = NoteIn.model_validate({k: v for k, v in payload.items() if k not in _META})
            if (note.kind == "relation") != (kind == "relation"):
                raise HTTPException(422, "kind must match the note kind")
            note.visibility = note.visibility or "private"
            note.label_id = None  # agents never choose a note label: inherit
            service.check(session, p, doc_id, note)
            stored = {"document_id": str(doc_id), **note.model_dump(mode="json", exclude_none=True)}
    except ValidationError as e:
        raise _422(e) from None
    except (KeyError, ValueError):
        raise HTTPException(422, "invalid proposal payload") from None
    return _store(session, p, kind, stored)


def _label_payload(session: Session, p: Principal, payload: dict) -> dict:
    """{document_id, annotation_id?, label_id, reason?}: target readable by the proposer (agent gate
    included), label known. Whether the change is allowed is decided at approval, by the approver."""
    doc_id, label_id = UUID(str(payload["document_id"])), UUID(str(payload["label_id"]))
    if not doc_readable(session, p, doc_id):
        raise service.NOT_FOUND
    label_service.resolve(Policy.load(session), label_id)
    out = {"document_id": str(doc_id), "label_id": str(label_id)}
    if payload.get("annotation_id"):
        n = get_readable(session, p, UUID(str(payload["annotation_id"])))
        if n.document_id != doc_id:
            raise service.NOT_FOUND
        if n.parent_id is not None:
            raise HTTPException(422, "labels apply to thread roots, not replies")
        out["annotation_id"] = str(n.id)
    return out  # agent-supplied reason is dropped: the approver supplies it


def _store(session: Session, p: Principal, kind: str, stored: dict) -> Proposal:
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('proposals:' || :u))"), {"u": str(p.user_id)})
    pending = session.scalar(
        select(func.count()).where(
            Proposal.user_id == p.user_id, Proposal.client == p.client, Proposal.status == "pending"
        )
    )
    if pending >= PENDING_MAX:
        raise HTTPException(429, "too many pending proposals")
    row = Proposal(user_id=p.user_id, client=p.client, kind=kind, payload=stored)
    session.add(row)
    session.flush()
    session.refresh(row)
    emit(session, p.user_id, "proposal.new", {"proposal_id": str(row.id), "kind": kind, "client": p.client})
    return row


def get_own(session: Session, p: Principal, proposal_id: UUID) -> Proposal:
    row = session.scalar(
        select(Proposal).where(Proposal.id == proposal_id, Proposal.user_id == p.user_id).with_for_update()
    )
    if row is None:
        raise service.NOT_FOUND
    return row


def _pending(row: Proposal) -> None:
    if row.status != "pending":
        raise HTTPException(409, f"proposal already {row.status}")


def _approve_label(session: Session, p: Principal, row: Proposal, reason: str | None) -> Annotation | None:
    """Re-runs the set_*_label rules as the APPROVER (owner lowering -> 403; admin needs a reason when
    the label demands one). Returns the note for note-label proposals."""
    pl = row.payload
    if pl.get("annotation_id"):
        note = get_readable(session, p, UUID(pl["annotation_id"]))
        label_service.set_note_label(session, p, note, pl["label_id"], reason, via="proposal")
        return note
    doc = session.scalar(select(Document).where(Document.id == UUID(pl["document_id"])).with_for_update())
    if doc is None or not doc_readable(session, p, doc.id):
        raise service.NOT_FOUND
    old = doc.label_id
    label_service.set_document_label(session, p, doc, pl["label_id"], reason, via="proposal")
    if doc.label_id != old:  # ES converges through the acl_sync job; Postgres gates are already live
        enqueue(session, "acl_sync", {"doc_id": str(doc.id)})
    return None


def approve(
    session: Session, p: Principal, proposal_id: UUID, edits: ApproveIn | None = None
) -> Annotation | None:
    row = get_own(session, p, proposal_id)
    _pending(row)
    e = edits or ApproveIn()
    pl = row.payload
    try:
        if row.kind == "label":
            n = _approve_label(session, p, row, e.reason)
        elif pl.get("parent_id"):
            if e.visibility is not None or e.shares is not None:
                raise HTTPException(422, "replies follow the root")
            n = service.reply(
                session, p, UUID(pl["parent_id"]), ReplyIn(body=e.body or pl["body"]), agent_assisted=True
            )
        else:
            data = {k: v for k, v in pl.items() if k not in _META}
            data.update(e.model_dump(exclude_none=True))
            n = service.create(
                session,
                p,
                UUID(pl["document_id"]),
                NoteIn.model_validate(data),
                agent_assisted=True,
                default_visibility="private",
            )
    except ValidationError as err:
        raise _422(err) from None
    row.status, row.annotation_id, row.decided_at = "approved", n.id if n else None, datetime.now(UTC)
    audit(
        session,
        p,
        "proposal.approve",
        str(row.id),
        {"annotation_id": str(n.id) if n else None, "client": row.client},
    )
    session.flush()
    return n


def reject(session: Session, p: Principal, proposal_id: UUID) -> None:
    row = get_own(session, p, proposal_id)
    _pending(row)
    row.status, row.decided_at = "rejected", datetime.now(UTC)
    audit(session, p, "proposal.reject", str(row.id), {"client": row.client})
    session.flush()


def bulk_approve(session: Session, p: Principal, ids: list[UUID]) -> dict:
    ok, failed = [], []
    for pid in ids:
        try:
            with session.begin_nested():
                n = approve(session, p, pid)
                ok.append({"proposal_id": str(pid), "annotation_id": str(n.id) if n else None})
        except HTTPException as e:
            failed.append({"proposal_id": str(pid), "detail": str(e.detail)})
        except (ValidationError, SQLAlchemyError):
            failed.append({"proposal_id": str(pid), "detail": "invalid proposal"})
    return {"approved": ok, "failed": failed}
