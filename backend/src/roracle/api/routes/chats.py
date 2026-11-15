"""Chats: create, and stream cited answers over SSE (history/sharing/export land in Task 4)."""

from collections.abc import AsyncIterator, Iterator
from typing import Annotated
from uuid import UUID

import anyio
from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.api.routes.library import get_es
from roracle.api.routes.search import _own_collections, get_llm
from roracle.auth import Principal, current_principal, get_app_settings
from roracle.chat.answer import build_messages, stream_answer
from roracle.chat.retrieve import Viewer, retrieve_for_chat
from roracle.config import Settings
from roracle.db import get_session
from roracle.models import Chat, ChatMessage

router = APIRouter(prefix="/chats")
Who = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Session, Depends(get_session)]
Cfg = Annotated[Settings, Depends(get_app_settings)]
SCOPE = r"^(all|open|collection:[0-9a-fA-F-]{36})$"


class ChatIn(BaseModel):
    title: str = Field(default="", max_length=200)
    scope: str = Field(default="all", pattern=SCOPE)
    model: str | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    viewer: Viewer = Field(default_factory=Viewer)
    model: str | None = None


def chat_json(c: Chat) -> dict:
    return {
        "id": str(c.id),
        "title": c.title,
        "scope": c.scope,
        "model": c.model,
        "pins": c.pins,
        "excludes": c.excludes,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def _check_model(settings: Settings, model: str | None) -> None:
    if model is not None and model not in settings.chat_model_list:
        raise HTTPException(422, "unknown model")


@router.post("", status_code=201)
def create_chat(body: ChatIn, p: Who, session: Db, settings: Cfg) -> dict:
    _check_model(settings, body.model)
    if body.scope.startswith("collection:"):
        cid = body.scope.partition(":")[2]
        try:
            UUID(cid)
        except ValueError:
            raise HTTPException(422, "invalid scope") from None
        if not _own_collections(session, p, [cid])[0]:
            raise HTTPException(422, "unknown collection")
    chat = Chat(owner_id=p.user_id, title=body.title, scope=body.scope, model=body.model)
    session.add(chat)
    session.commit()
    session.refresh(chat)
    return chat_json(chat)


def _get_chat(session: Session, p: Principal, chat_id: UUID) -> Chat:
    chat = session.get(Chat, chat_id)
    if chat is None or chat.owner_id != p.user_id:  # Task 4: shared readers (then 403 on write)
        raise HTTPException(404, "not found")
    return chat


async def _frames(gen: Iterator[str]) -> AsyncIterator[str]:
    """Pull the sync generator off the event loop; on disconnect/cancel close it (-> `interrupted`)."""
    try:
        while (f := await anyio.to_thread.run_sync(next, gen, None)) is not None:
            yield f
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(gen.close)


@router.post("/{chat_id}/messages")
def post_message(
    chat_id: UUID,
    body: MessageIn,
    p: Who,
    session: Db,
    settings: Cfg,
    es: Annotated[Elasticsearch, Depends(get_es)],
    llm: Annotated[object, Depends(get_llm)],
) -> StreamingResponse:
    chat = _get_chat(session, p, chat_id)
    _check_model(settings, body.model)
    model = body.model or chat.model or settings.chat_model_list[0]
    history = list(
        session.scalars(
            select(ChatMessage)
            .where(ChatMessage.chat_id == chat.id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        )
    )
    try:
        passages = retrieve_for_chat(
            session, es, llm, settings, p, body.content, body.viewer, chat.scope,
            list(chat.pins), list(chat.excludes),
        )  # fmt: skip
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    session.add(
        ChatMessage(chat_id=chat.id, role="user", content=body.content, viewer=body.viewer.model_dump())
    )
    if not chat.title:
        chat.title = body.content[:80]
    session.commit()
    messages = build_messages(body.content, passages, history)
    gen = stream_answer(session, llm, chat.id, model, messages, passages)
    return StreamingResponse(
        _frames(gen),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
