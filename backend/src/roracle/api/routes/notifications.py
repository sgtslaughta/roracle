from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from roracle.access import readable_documents
from roracle.auth import Principal, current_principal
from roracle.db import get_session
from roracle.models import Document, Notification

router = APIRouter()


class ReadBody(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=1000)
    all: bool = False


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
    unread = session.scalar(
        select(func.count()).select_from(Notification).where(mine, Notification.read_at.is_(None))
    )
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

    return {
        "max_id": max_id,
        "items": [
            {
                "id": n.id,
                "kind": n.kind,
                "payload": payload(n),
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
