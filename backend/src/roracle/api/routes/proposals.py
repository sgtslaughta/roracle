"""Agent proposals REST (P8 task 4). Decisions are human-only (R3)."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from roracle.access import visible_documents
from roracle.auth import Principal, current_principal, require_human
from roracle.db import get_session
from roracle.models import Document, Proposal
from roracle.notes import proposals, service
from roracle.notes.access import readable_doc_ids

router = APIRouter()
Session_ = Annotated[Session, Depends(get_session)]
Who = Annotated[Principal, Depends(current_principal)]
Human = Annotated[Principal, Depends(require_human)]
LIST_MAX = 200


class BulkIn(BaseModel):
    ids: list[UUID] = Field(max_length=100)


def _agent_scope(p: Principal):
    """PAT callers see only proposals on documents visible to agents (ids of hidden ones would leak)."""
    if p.via != "pat":
        return ()
    return (
        Proposal.payload["document_id"].astext.in_(
            select(cast(Document.id, String)).where(visible_documents(p))
        ),
    )


def view(row: Proposal, docs: dict[str, str | None]) -> dict:
    did = row.payload.get("document_id")
    return {
        "id": str(row.id),
        "kind": row.kind,
        "client": row.client,
        "payload": row.payload,
        "status": row.status,
        "annotation_id": str(row.annotation_id) if row.annotation_id else None,
        "created_at": row.created_at.isoformat(),
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "document": {"id": did, "title": docs[did]} if did in docs else None,
    }


@router.get("/me/proposals")
def my_proposals(
    p: Who,
    session: Session_,
    status: Annotated[Literal["pending", "approved", "rejected"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=LIST_MAX)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    q = select(Proposal).where(Proposal.user_id == p.user_id, *_agent_scope(p))
    if status:
        q = q.where(Proposal.status == status)
    total = session.scalar(select(func.count()).select_from(q.subquery()))
    rows = list(
        session.scalars(q.order_by(Proposal.created_at.desc(), Proposal.id).limit(limit).offset(offset))
    )
    wanted = {r.payload.get("document_id") for r in rows}
    docs = {
        str(i): t
        for i, t in session.execute(
            select(Document.id, Document.title).where(
                Document.id.in_([UUID(w) for w in wanted if w]), Document.id.in_(readable_doc_ids(p))
            )
        )
    }
    return {"total": total, "items": [view(r, docs) for r in rows]}


@router.get("/me/proposals/count")
def my_proposals_count(p: Who, session: Session_) -> dict:
    n = session.scalar(
        select(func.count())
        .select_from(Proposal)
        .where(Proposal.user_id == p.user_id, Proposal.status == "pending", *_agent_scope(p))
    )
    return {"pending": n}


@router.post("/proposals/bulk-approve")
def bulk_approve(body: BulkIn, session: Session_, p: Human) -> dict:
    out = proposals.bulk_approve(session, p, body.ids)
    session.commit()
    return out


@router.post("/proposals/{proposal_id}/approve")
def approve(proposal_id: UUID, session: Session_, p: Human, body: proposals.ApproveIn | None = None) -> dict:
    n = proposals.approve(session, p, proposal_id, body)
    if n is None:  # label proposal on a document
        out = {"status": "approved", "kind": "label"}
    else:
        out = service.to_json(session, p, [n])[0]
    session.commit()
    return out


@router.post("/proposals/{proposal_id}/reject", status_code=204)
def reject(proposal_id: UUID, session: Session_, p: Human) -> Response:
    proposals.reject(session, p, proposal_id)
    session.commit()
    return Response(status_code=204)
