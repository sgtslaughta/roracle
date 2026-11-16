"""Chats: create, and stream cited answers over SSE (read/share/export: chats_read)."""

import contextlib
from collections.abc import AsyncIterator, Iterator
from typing import Annotated
from uuid import UUID

import anyio
import httpx
import openai
from elasticsearch import ConnectionError as EsConnectionError
from elasticsearch import Elasticsearch, TransportError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from roracle.api.routes.library import get_es
from roracle.api.routes.search import _own_collections, get_llm
from roracle.auth import Principal, current_principal, get_app_settings
from roracle.chat.access import owned_chat
from roracle.chat.answer import build_messages, split_notes, stream_answer
from roracle.chat.retrieve import Viewer, has_open_context, open_blocked, retrieve_for_chat
from roracle.config import Settings
from roracle.db import get_session
from roracle.labels.textgate import replay_history
from roracle.models import Chat, ChatMessage, DocumentVersion

router = APIRouter(prefix="/chats")
Who = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Session, Depends(get_session)]
Cfg = Annotated[Settings, Depends(get_app_settings)]
HISTORY = 6
NO_OPEN_DOCS = "No open documents to answer from. Open a document or switch scope."
NO_READABLE = "None of the open documents can be used as a source for answers."
SCOPE = r"^(all|open|collection:[0-9a-fA-F-]{36})$"


class ChatIn(BaseModel):
    title: str = Field(default="", max_length=200)
    scope: str = Field(default="all", pattern=SCOPE)
    model: str | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    viewer: Viewer = Field(default_factory=Viewer)
    model: str | None = None
    include_notes: bool = True


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


def check_scope(session: Session, p: Principal, scope: str) -> None:
    if scope.startswith("collection:"):
        cid = scope.partition(":")[2]
        try:
            UUID(cid)
        except ValueError:
            raise HTTPException(422, "invalid scope") from None
        if not _own_collections(session, p, [cid])[0]:
            raise HTTPException(422, "unknown collection")


@router.post("", status_code=201)
def create_chat(body: ChatIn, p: Who, session: Db, settings: Cfg) -> dict:
    _check_model(settings, body.model)
    check_scope(session, p, body.scope)
    chat = Chat(owner_id=p.user_id, title=body.title, scope=body.scope, model=body.model)
    session.add(chat)
    session.commit()
    session.refresh(chat)
    return chat_json(chat)


class ClosingStreamingResponse(StreamingResponse):
    """Always aclose() the body iterator, so a disconnect reliably runs `_frames`' finally."""

    async def stream_response(self, send) -> None:
        async with contextlib.aclosing(self.body_iterator):
            await super().stream_response(send)


async def _frames(gen: Iterator[str]) -> AsyncIterator[str]:
    """Pull the sync generator off the event loop; on disconnect/cancel close it (-> `interrupted`).

    Pulls must keep the default abandon_on_cancel=False: close() must never race a running next().
    """
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
    chat = owned_chat(session, p, chat_id)
    _check_model(settings, body.model)
    allowed = settings.chat_model_list
    model = body.model or (chat.model if chat.model in allowed else allowed[0])
    recent = session.scalars(
        select(ChatMessage)
        .where(ChatMessage.chat_id == chat.id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(2 * HISTORY)
    )
    history = replay_history(session, p, list(reversed(list(recent))))
    canned = None
    passages = []
    excluded: list[dict] = []
    if chat.scope == "open" and not has_open_context(session, p, body.viewer):
        excluded = open_blocked(session, p, body.viewer)
        canned, model = (NO_READABLE if excluded else NO_OPEN_DOCS), None
    else:
        try:
            passages = retrieve_for_chat(
                session, es, llm, settings, p, body.content, body.viewer, chat.scope,
                list(chat.pins), list(chat.excludes), include_notes=body.include_notes,
                excluded=excluded,
            )  # fmt: skip
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except (openai.APIError, httpx.HTTPError):
            raise HTTPException(503, "inference unavailable") from None
        except (EsConnectionError, TransportError):
            raise HTTPException(503, "search unavailable") from None
    session.add(
        ChatMessage(chat_id=chat.id, role="user", content=body.content, viewer=body.viewer.model_dump())
    )
    if not chat.title:
        chat.title = body.content[:80]
    session.commit()
    passages, notes = split_notes(passages)
    messages = build_messages(body.content, passages, history, HISTORY, notes)
    vids = [UUID(x.version_id) for x in (*passages, *notes)]
    versions = {
        str(i): n
        for i, n in session.execute(
            select(DocumentVersion.id, DocumentVersion.version).where(DocumentVersion.id.in_(vids))
        )
    }
    gen = stream_answer(session, llm, chat.id, model, messages, passages, versions, canned, notes, excluded)
    return ClosingStreamingResponse(
        _frames(gen),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
