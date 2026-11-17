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
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from roracle.api.routes.library import get_es
from roracle.api.routes.search import _own_collections, get_llm
from roracle.auth import Principal, current_principal, get_app_settings
from roracle.chat import agentic, summarize
from roracle.chat.access import owned_chat
from roracle.chat.answer import build_messages, frame, split_notes, stream_answer
from roracle.chat.global_answer import route_question, stream_prepare
from roracle.chat.retrieve import Viewer, _uids, has_open_context, open_blocked, retrieve_for_chat
from roracle.config import Settings
from roracle.db import get_session, get_stream_session
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

    def __init__(self, *a, closer=None, **kw):
        super().__init__(*a, **kw)
        self.closer = closer  # sync close of the answer stream: runs even if iteration never started

    async def stream_response(self, send) -> None:
        try:
            async with contextlib.aclosing(self.body_iterator):
                await super().stream_response(send)
        finally:
            if self.closer:
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(self.closer)


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


def _versions(session: Session, passages: list) -> dict[str, int]:
    vids = [UUID(x.version_id) for x in passages]
    if not vids:
        return {}
    rows = session.execute(
        select(DocumentVersion.id, DocumentVersion.version).where(DocumentVersion.id.in_(vids))
    )
    return {str(i): n for i, n in rows}


def _release(prep, ss: Session, own: bool):
    def go() -> None:
        prep.release()
        if own:
            ss.close()

    return go


def _close(ss: Session):
    return ss.close


def _leave(p: Principal, ss: Session, own: bool, token: int):
    def go() -> None:
        agentic.leave(p, token)
        if own:
            ss.close()

    return go


def _fact_fallback(
    ss: Session, es, llm, settings: Settings, p: Principal, body: "MessageIn", chat: Chat, history
):
    """Inside a global stream whose map phase produced nothing usable: the ordinary retrieval + prompt, on the
    stream's own session. Returns (messages, passages, versions, notes, excluded)."""
    pins, excludes, scope = list(chat.pins), list(chat.excludes), chat.scope

    def run():
        excluded: list[dict] = []
        ps = retrieve_for_chat(
            ss, es, llm, settings, p, body.content, body.viewer, scope, pins, excludes,
            include_notes=body.include_notes, excluded=excluded,
        )  # fmt: skip
        ps, notes = split_notes(ps)
        out = (
            build_messages(body.content, ps, history, HISTORY, notes),
            ps,
            _versions(ss, [*ps, *notes]),
            notes,
            excluded,
        )
        ss.commit()
        return out

    return run


def _agentic_prepare(
    ss: Session, es, llm, settings: Settings, p: Principal, body: "MessageIn", chat: Chat, history, factory
):
    """For `AnswerStream(prepare=...)`: the agentic retrieval loop INSIDE the stream, after the first `meta` frame
    (no pooled connection is held while the planner model runs). Yields `progress` frames, then a `meta` frame with the
    sub-queries; returns (messages, passages, versions, canned, notes). Retrieval uses the stream's own session."""

    pins, excludes, scope = (
        list(chat.pins),
        list(chat.excludes),
        chat.scope,
    )  # read now: `chat` is the request's

    def run():
        ss.commit()
        gen = agentic.iter_retrieve(
            ss,
            es,
            llm,
            settings,
            p,
            body.content,
            viewer=body.viewer,
            scope=scope,
            excludes=excludes,
            session_factory=factory,
        )
        try:
            while True:
                step, of, done = next(gen)
                yield frame("progress", {"step": step, "of": of, "done": done})
        except StopIteration as e:
            ranked, subqueries = e.value
        excluded: list[dict] = []
        ps = retrieve_for_chat(
            ss, es, llm, settings, p, body.content, body.viewer, scope, pins, excludes,
            include_notes=body.include_notes, excluded=excluded, ranked=ranked,
        )  # fmt: skip
        ps, notes = split_notes(ps)
        msgs = build_messages(body.content, ps, history, HISTORY, notes)
        versions = _versions(ss, [*ps, *notes])
        ss.commit()
        yield frame(
            "meta", {"subqueries": subqueries, **({"excluded_by_label": excluded} if excluded else {})}
        )
        return msgs, ps, versions, None, notes

    return run


@router.post("/{chat_id}/messages")
def post_message(
    chat_id: UUID,
    body: MessageIn,
    p: Who,
    session: Db,
    settings: Cfg,
    es: Annotated[Elasticsearch, Depends(get_es)],
    llm: Annotated[object, Depends(get_llm)],
    ss: Annotated[Session, Depends(get_stream_session)],
    request: Request = None,  # type: ignore[assignment]  # injected by FastAPI; direct calls in tests omit it
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
    meta: dict | None = None
    prep = None
    agent = False
    target = None
    token = 0  # agentic admission token
    if chat.scope == "open" and not has_open_context(session, p, body.viewer):
        excluded = open_blocked(session, p, body.viewer)
        canned, model = (NO_READABLE if excluded else NO_OPEN_DOCS), None
    elif (dec := summarize.decide(session, p, body.content, body.viewer, chat.scope, chat.excludes)) != (
        None,
        False,
    ):
        tv, _guard = dec  # open-paper rule: no corpus search, no global routing
        if tv is not None:
            target = tv.document_id
        else:  # contentless and nothing usable open: canned guard, no LLM, no search
            excluded = open_blocked(session, p, body.viewer)
            canned, model = summarize.GUARD, None
    else:
        try:
            if settings.router:
                prep, meta = route_question(
                    session, es, llm, settings, p, body.content,
                    chat.scope == "all" and not body.viewer.tabs and not body.viewer.selection,
                    dropped=set(_uids(chat.excludes)) | set(_uids(body.viewer.ignored)), pins=_uids(chat.pins),
                )  # fmt: skip
            if (
                prep is None
                and settings.agentic_retrieval
                and (meta is None or meta.get("route") == "multihop")
            ):
                token = agentic.enter(settings, p)  # busy / already running for this user: the plain path
                agent = bool(token)
                meta = {**(meta or {}), "agentic": agent}
            if prep is None and not agent:
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
    try:
        session.add(
            ChatMessage(chat_id=chat.id, role="user", content=body.content, viewer=body.viewer.model_dump())
        )
        if not chat.title:
            chat.title = body.content[:80]
        chat.hidden_at = None  # in use again
        session.commit()  # no transaction (and no pooled connection) stays open into the stream
        passages, notes = split_notes(passages)
        hist = [{"role": m.role, "content": m.content} for m in history if m.content][-HISTORY:]
        messages = (
            [] if prep or agent or target else build_messages(body.content, passages, history, HISTORY, notes)
        )
        versions = _versions(session, [*passages, *notes])
        fact = None
        if prep:  # the global stream gets its OWN session (independent of the request's teardown timing)
            fact = _fact_fallback(ss, es, llm, settings, p, body, chat, history)
        gen = stream_answer(
            ss if prep or agent or target else session, llm, chat.id, model, messages, passages, versions, canned, notes,
            excluded, meta, settings.global_reduce_tokens if prep else None,
            stream_prepare(ss, es, llm, settings, p, body.content, prep, hist, fact)
            if prep
            else summarize.prepare(ss, es, llm, settings, p, body.content, target, history, HISTORY)
            if target
            else (_agentic_prepare(ss, es, llm, settings, p, body, chat, history, request.app.state.session_factory if request else None) if agent else None),
            _release(prep, ss, ss is not session) if prep else (_leave(p, ss, ss is not session, token) if agent else (_close(ss) if ss is not session and target else None)),
        )  # fmt: skip
    except BaseException:
        if prep:
            prep.release()
        if agent:
            agentic.leave(p, token)
        raise
    return ClosingStreamingResponse(
        _frames(gen),
        closer=gen.close,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
