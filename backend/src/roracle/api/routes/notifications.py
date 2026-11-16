from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from roracle.access import agent_visible, readable_documents
from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.models import Document, Notification
from roracle.notes.community import readable_note_ids

router = APIRouter()


class ReadBody(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=1000)
    all: bool = False


_DOC_KEYS = ("document_id", "draft_id", "match_document_id")
_NOTE_KEYS = ("note_id", "annotation_id")


def _agent_hidden(session: Session, p: Principal, page: list[Notification]) -> set[int]:
    """PAT callers: ids of notifications that name a document hidden from agents (titles/ids would leak)."""
    if p.via != "pat":
        return set()
    refs: dict[int, set[UUID]] = {}
    for n in page:
        for k in _DOC_KEYS:
            try:
                refs.setdefault(n.id, set()).add(UUID(str(n.payload[k])))
            except (KeyError, ValueError):
                pass
    ids = set().union(*refs.values()) if refs else set()
    bad = (
        set(session.scalars(select(Document.id).where(Document.id.in_(ids), ~agent_visible())))
        if ids
        else set()
    )
    hidden = {i for i, docs in refs.items() if docs & bad}
    notes = {i: {str(n.payload[k]) for k in _NOTE_KEYS if n.payload.get(k)} for n in page for i in (n.id,)}
    every = set().union(*notes.values()) if notes else set()
    if every:  # a note hidden from agents (own label block_agents, or its document's)
        ok = readable_note_ids(session, p, every)
        hidden |= {i for i, ns in notes.items() if ns - ok}
    return hidden


@router.get("/me/notifications")
def list_notifications(
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
    unread_only: bool = False,
    before_id: int | None = None,
    since_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """Newest first. `since_id` polls for newer rows: if more than `limit` are new, the page holds the newest
    `limit` and the older gap is reachable via `next_before_id` (page back until it is null or <= since_id)."""
    mine = Notification.user_id == p.user_id
    q = select(Notification).where(mine).order_by(Notification.id.desc()).limit(limit + 1)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    if before_id is not None:
        q = q.where(Notification.id < before_id)
    if since_id is not None:
        q = q.where(Notification.id > since_id)
    rows = list(session.scalars(q))
    page = rows[:limit]
    unread_q = select(Notification).where(mine, Notification.read_at.is_(None))
    if p.via == "pat":  # hidden rows must not be countable either
        every = list(session.scalars(unread_q.limit(5000)))
        unread = len(every) - len(_agent_hidden(session, p, every))
    else:
        unread = session.scalar(select(func.count()).select_from(unread_q.subquery()))
    max_id = session.scalar(select(func.max(Notification.id)).where(mine))
    ids = {
        UUID(str(n.payload["match_document_id"]))
        for n in page
        if n.kind == "upload.near_dup" and n.payload.get("match_document_id")
    }
    titles = (
        dict(
            session.execute(
                select(Document.id, Document.title).where(Document.id.in_(ids), readable_documents(p))
            ).all()
        )
        if ids
        else {}
    )

    def payload(n: Notification) -> dict:
        if n.kind != "upload.near_dup":
            return n.payload
        mid = n.payload.get("match_document_id")
        readable = bool(mid) and UUID(str(mid)) in titles
        title = titles[UUID(str(mid))] if readable else None
        return {
            **{k: v for k, v in n.payload.items() if k not in ("match_title", "match_document_id")},
            "match_document_id": mid if readable else None,
            "match_title": title,
        }

    hidden = _agent_hidden(session, p, page)

    return {
        "max_id": max_id,
        "items": [
            {
                "id": n.id,
                "kind": n.kind,
                "payload": {"withheld": True} if n.id in hidden else payload(n),
                "created_at": n.created_at,
                "read_at": n.read_at,
            }
            for n in page
        ],
        "next_before_id": page[-1].id if len(rows) > limit else None,
        "unread": unread,
    }


@router.post("/me/notifications/read")
def mark_read(
    body: ReadBody,
    p: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    q = update(Notification).where(Notification.user_id == p.user_id, Notification.read_at.is_(None))
    if not body.all:
        q = q.where(Notification.id.in_(body.ids))
    return {"updated": session.execute(q.values(read_at=func.now())).rowcount}
