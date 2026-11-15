"""Chats: history, edit, read-only sharing, Markdown export, ratings (citations re-checked per reader)."""

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from roracle.api.routes.chats import SCOPE, chat_json, check_scope
from roracle.auth import Principal, current_principal, get_app_settings
from roracle.chat.access import owned_chat, readable_chat, recheck, visible_versions
from roracle.config import Settings
from roracle.db import get_session
from roracle.models import AnswerRating, Chat, ChatMessage, ChatShare, User

router = APIRouter()
Who = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Session, Depends(get_session)]
Cfg = Annotated[Settings, Depends(get_app_settings)]


def _uuids(values: list[str]) -> list[str]:
    try:
        return list(dict.fromkeys(str(UUID(v)) for v in values))
    except ValueError:
        raise HTTPException(422, "invalid id") from None


def _norm_group(g: str) -> str:
    g = re.sub(r"/{2,}", "/", "/" + g.strip()).rstrip("/")
    return g


@router.get("/chats/models")
def models(p: Who, settings: Cfg) -> dict:
    allowed = settings.chat_model_list
    return {"models": allowed, "default": allowed[0] if allowed else None}


@router.get("/chats")
def list_chats(
    p: Who,
    session: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: datetime | None = None,
) -> dict:
    q = select(Chat).where(Chat.owner_id == p.user_id)
    if before is not None:
        q = q.where(Chat.updated_at < before)
    rows = list(session.scalars(q.order_by(Chat.updated_at.desc(), Chat.id.desc()).limit(limit)))
    nxt = rows[-1].updated_at.isoformat() if len(rows) == limit else None
    return {"items": [chat_json(c) for c in rows], "next": nxt}


def _messages(session: Session, p: Principal, chat: Chat) -> list[dict]:
    msgs = list(
        session.scalars(
            select(ChatMessage)
            .where(ChatMessage.chat_id == chat.id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        )
    )
    ok = visible_versions(session, p, [x for m in msgs for x in (*m.citations, *m.retrieved)])
    mine = {
        r.message_id: r
        for r in session.scalars(
            select(AnswerRating).where(
                AnswerRating.user_id == p.user_id, AnswerRating.message_id.in_([m.id for m in msgs])
            )
        )
    }
    owner = chat.owner_id == p.user_id
    out = []
    for m in msgs:
        r = mine.get(m.id)
        out.append(
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "status": m.status,
                "model": m.model,
                "created_at": m.created_at,
                "viewer": m.viewer if m.role == "user" and owner else None,
                "citations": recheck(m.citations, ok, drop=False),
                "retrieved": recheck(m.retrieved, ok, drop=True),
                "rating": {"value": r.value, "comment": r.comment} if r else None,
            }
        )
    return out


@router.get("/chats/{chat_id}")
def get_chat(chat_id: UUID, p: Who, session: Db) -> dict:
    chat = readable_chat(session, p, chat_id)
    return {
        **chat_json(chat),
        "is_owner": chat.owner_id == p.user_id,
        "messages": _messages(session, p, chat),
    }


class ChatPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    scope: str | None = Field(default=None, pattern=SCOPE)
    pins: list[str] | None = Field(default=None, max_length=50)
    excludes: list[str] | None = Field(default=None, max_length=200)
    model: str | None = None


@router.patch("/chats/{chat_id}")
def patch_chat(chat_id: UUID, body: ChatPatch, p: Who, session: Db, settings: Cfg) -> dict:
    chat = owned_chat(session, p, chat_id)
    if body.model is not None and body.model not in settings.chat_model_list:
        raise HTTPException(422, "unknown model")
    if body.scope is not None:
        check_scope(session, p, body.scope)
        chat.scope = body.scope
    if body.title is not None:
        chat.title = body.title
    if body.pins is not None:
        chat.pins = _uuids(body.pins)
    if body.excludes is not None:
        chat.excludes = _uuids(body.excludes)
    if body.model is not None:
        chat.model = body.model
    session.commit()
    session.refresh(chat)
    return chat_json(chat)


class SharesIn(BaseModel):
    users: list[str] = Field(default_factory=list, max_length=100)
    groups: list[str] = Field(default_factory=list, max_length=100)


def _shares(session: Session, chat_id: UUID) -> dict:
    rows = session.execute(
        select(ChatShare.principal_kind, ChatShare.principal).where(ChatShare.chat_id == chat_id)
    ).all()
    return {
        "users": sorted(v for k, v in rows if k == "user"),
        "groups": sorted(v for k, v in rows if k == "group"),
    }


@router.get("/chats/{chat_id}/shares")
def get_shares(chat_id: UUID, p: Who, session: Db) -> dict:
    owned_chat(session, p, chat_id)
    return _shares(session, chat_id)


@router.put("/chats/{chat_id}/shares")
def put_shares(chat_id: UUID, body: SharesIn, p: Who, session: Db) -> dict:
    chat = owned_chat(session, p, chat_id)
    try:
        users = {UUID(u) for u in body.users} - {p.user_id}
    except ValueError:
        raise HTTPException(422, "unknown user") from None
    if users and len(set(session.scalars(select(User.id).where(User.id.in_(users))))) != len(users):
        raise HTTPException(422, "unknown user")
    groups = {g for g in map(_norm_group, body.groups) if g}
    session.execute(delete(ChatShare).where(ChatShare.chat_id == chat.id))
    session.add_all(ChatShare(chat_id=chat.id, principal_kind="user", principal=str(u)) for u in users)
    session.add_all(ChatShare(chat_id=chat.id, principal_kind="group", principal=g) for g in groups)
    session.commit()
    return _shares(session, chat.id)


def _reference(c: dict) -> str:
    if not c.get("available"):
        return f"[{c['n']}] Source not available"
    parts = [", ".join(c.get("authors") or []), c.get("title") or "", str(c["year"]) if c.get("year") else ""]
    parts.append(f"p. {c['page']}" if c.get("page") else "")
    return f"[{c['n']}] " + ", ".join(x for x in parts if x)


@router.get("/chats/{chat_id}/export.md")
def export_chat(chat_id: UUID, p: Who, session: Db) -> Response:
    chat = readable_chat(session, p, chat_id)
    lines = [f"# {chat.title or 'Chat'}", ""]
    for m in _messages(session, p, chat):
        lines += [f"**{'You' if m['role'] == 'user' else 'rOracle'}:** {m['content']}", ""]
        if m["role"] == "assistant" and m["citations"]:
            lines += ["References", "", *(_reference(c) for c in m["citations"]), ""]
    return Response(
        "\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="chat-{chat.id}.md"'},
    )


class RatingIn(BaseModel):
    value: Literal[-1, 0, 1]
    comment: str | None = Field(default=None, max_length=2000)


@router.put("/messages/{message_id}/rating")
def rate(message_id: UUID, body: RatingIn, p: Who, session: Db) -> dict:
    msg = session.get(ChatMessage, message_id)
    if msg is None:
        raise HTTPException(404, "not found")
    readable_chat(session, p, msg.chat_id)
    if msg.role != "assistant":
        raise HTTPException(422, "only assistant messages can be rated")
    row = session.get(AnswerRating, (message_id, p.user_id))
    if body.value == 0:
        if row is not None:
            session.delete(row)
        session.commit()
        return {"value": 0, "comment": None}
    if row is None:
        session.add(
            AnswerRating(message_id=message_id, user_id=p.user_id, value=body.value, comment=body.comment)
        )
    else:
        row.value, row.comment = body.value, body.comment
    session.commit()
    return {"value": body.value, "comment": body.comment}


@router.get("/admin/ratings")
def admin_ratings(p: Who, session: Db, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict]:
    if not p.is_admin:
        raise HTTPException(403, "admin only")
    rows = session.execute(
        select(AnswerRating, ChatMessage, User.username)
        .join(ChatMessage, ChatMessage.id == AnswerRating.message_id)
        .join(User, User.id == AnswerRating.user_id)
        .order_by(AnswerRating.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "message_id": str(r.message_id),
            "chat_id": str(m.chat_id),
            "user_id": str(r.user_id),
            "username": name,
            "value": r.value,
            "comment": r.comment,
            "created_at": r.created_at,
            "model": m.model,
            "answer": m.content,
        }
        for r, m, name in rows
    ]
