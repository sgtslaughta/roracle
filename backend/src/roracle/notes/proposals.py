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
from roracle.models import Annotation, Proposal
from roracle.notes import service
from roracle.notes.access import get_readable
from roracle.notes.schemas import BODY_MAX, SHARES_MAX, NoteIn, ReplyIn, Share, Visibility
from roracle.notify import emit

PENDING_MAX = 50
_META = ("document_id", "parent_id")


class ApproveIn(BaseModel):
    body: str | None = Field(None, min_length=1, max_length=BODY_MAX)
    visibility: Visibility | None = None
    shares: list[Share] | None = Field(None, max_length=SHARES_MAX)


def _422(e: ValidationError) -> HTTPException:
    return HTTPException(422, "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))


def propose(session: Session, p: Principal, kind: str, payload: dict) -> Proposal:
    """payload = note fields + `document_id` (+ `parent_id` for a reply). Visibility defaults to private."""
    if kind not in ("note", "relation"):
        raise HTTPException(422, "proposal kind must be note or relation")
    try:
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
            service.check(session, p, doc_id, note)
            stored = {"document_id": str(doc_id), **note.model_dump(mode="json", exclude_none=True)}
    except ValidationError as e:
        raise _422(e) from None
    except (KeyError, ValueError):
        raise HTTPException(422, "invalid proposal payload") from None
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


def approve(session: Session, p: Principal, proposal_id: UUID, edits: ApproveIn | None = None) -> Annotation:
    row = get_own(session, p, proposal_id)
    _pending(row)
    e = edits or ApproveIn()
    pl = row.payload
    try:
        if pl.get("parent_id"):
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
    row.status, row.annotation_id, row.decided_at = "approved", n.id, datetime.now(UTC)
    audit(session, p, "proposal.approve", str(row.id), {"annotation_id": str(n.id), "client": row.client})
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
                ok.append({"proposal_id": str(pid), "annotation_id": str(approve(session, p, pid).id)})
        except HTTPException as e:
            failed.append({"proposal_id": str(pid), "detail": str(e.detail)})
        except (ValidationError, SQLAlchemyError):
            failed.append({"proposal_id": str(pid), "detail": "invalid proposal"})
    return {"approved": ok, "failed": failed}
