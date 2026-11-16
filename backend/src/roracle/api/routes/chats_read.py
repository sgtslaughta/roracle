"""Chats: history, edit, read-only sharing, Markdown export, ratings (citations re-checked per reader)."""

import base64
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.orm import Session
from starlette.responses import Response

from roracle.access import visible_documents
from roracle.api.routes.admin_deps import Admin
from roracle.api.routes.chats import SCOPE, _check_model, chat_json, check_scope
from roracle.api.routes.documents_write import Path_
from roracle.audit import audit
from roracle.auth import Principal, current_principal, get_app_settings
from roracle.chat.access import note_ids, owned_chat, readable_chat, recheck, visible_versions
from roracle.config import Settings
from roracle.db import get_session
from roracle.labels.policy import Policy, export_exempt
from roracle.labels.textgate import gated_refs, ref_ids_of, strip_text, withheld_text, withhold_messages
from roracle.models import Annotation, AnswerRating, Chat, ChatMessage, ChatShare, Document, User
from roracle.notes.community import readable_note_ids

MAX_MESSAGES = 1000  # per chat read/export; the newest are kept

router = APIRouter()
Who = Annotated[Principal, Depends(current_principal)]
Db = Annotated[Session, Depends(get_session)]
Cfg = Annotated[Settings, Depends(get_app_settings)]


def _uuids(values: list[str]) -> list[str]:
    try:
        return list(dict.fromkeys(str(UUID(v)) for v in values))
    except ValueError:
        raise HTTPException(422, "invalid id") from None


@router.get("/chats/models")
def models(p: Who, settings: Cfg) -> dict:
    allowed = settings.chat_model_list
    return {"models": allowed, "default": allowed[0] if allowed else None}


@router.get("/chats")
def list_chats(
    p: Who,
    session: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: str | None = None,
) -> dict:
    q = select(Chat).where(Chat.owner_id == p.user_id)
    if before is not None:
        try:
            ts, _, cid = base64.urlsafe_b64decode(before.encode()).decode().partition("|")
            q = q.where(tuple_(Chat.updated_at, Chat.id) < (datetime.fromisoformat(ts), UUID(cid)))
        except ValueError:  # includes bad base64/utf-8 and bad timestamp/uuid
            raise HTTPException(422, "invalid cursor") from None
    rows = list(session.scalars(q.order_by(Chat.updated_at.desc(), Chat.id.desc()).limit(limit)))
    nxt = None
    if len(rows) == limit:
        nxt = base64.urlsafe_b64encode(f"{rows[-1].updated_at.isoformat()}|{rows[-1].id}".encode()).decode()
    return {"items": [_agent_chat(session, p, chat_json(c)) for c in rows], "next": nxt}


def _agent_chat(session: Session, p: Principal, out: dict) -> dict:
    """PAT callers: drop pinned/excluded ids of documents hidden from agents (existence leak)."""
    if p.via != "pat":
        return out
    ids = [i for i in (*out["pins"], *out["excludes"]) if isinstance(i, str)]
    ok = set()
    if ids:
        try:
            ok = {
                str(i)
                for i in session.scalars(
                    select(Document.id).where(Document.id.in_([UUID(i) for i in ids]), visible_documents(p))
                )
            }
        except ValueError:
            ok = set()
    return {
        **out,
        "pins": [i for i in out["pins"] if i in ok],
        "excludes": [i for i in out["excludes"] if i in ok],
    }


def _messages(session: Session, p: Principal, chat: Chat) -> list[dict]:
    msgs = list(
        session.scalars(
            select(ChatMessage)
            .where(ChatMessage.chat_id == chat.id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(MAX_MESSAGES)
        )
    )[::-1]  # the newest MAX_MESSAGES, oldest first
    seen = [x for m in msgs for x in (*m.citations, *m.retrieved)]
    ok = visible_versions(session, p, seen)
    notes = readable_note_ids(session, p, note_ids(seen))
    mine = {
        r.message_id: r
        for r in session.scalars(
            select(AnswerRating).where(
                AnswerRating.user_id == p.user_id, AnswerRating.message_id.in_([m.id for m in msgs])
            )
        )
    }
    owner = chat.owner_id == p.user_id
    gated = gated_refs(session, p, seen)  # agents: no text derived from gated docs or notes
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
                "viewer": m.viewer
                if m.role == "user" and owner and p.via != "pat"
                else None,  # agents: no selection text
                "citations": strip_text(recheck(m.citations, ok, drop=False, notes=notes), gated),
                "retrieved": strip_text(recheck(m.retrieved, ok, drop=True, notes=notes), gated),
                "rating": {"value": r.value, "comment": r.comment} if r else None,
                "_docs": ref_ids_of((*m.citations, *m.retrieved)),
            }
        )
    withhold_messages(out, gated, lambda m: m["_docs"])
    for m in out:
        del m["_docs"]
    return out


@router.get("/chats/{chat_id}")
def get_chat(chat_id: UUID, p: Who, session: Db) -> dict:
    chat = readable_chat(session, p, chat_id)
    out = chat_json(chat)
    owner = chat.owner_id == p.user_id
    if not owner:  # the owner's context config is private to the owner
        out |= {
            "pins": [],
            "excludes": [],
            "scope": "collection" if chat.scope.startswith("collection:") else chat.scope,
        }
    return {**_agent_chat(session, p, out), "is_owner": owner, "messages": _messages(session, p, chat)}


class ChatPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    scope: str | None = Field(default=None, pattern=SCOPE)
    pins: list[str] | None = Field(default=None, max_length=50)
    excludes: list[str] | None = Field(default=None, max_length=200)
    model: str | None = None


@router.patch("/chats/{chat_id}")
def patch_chat(chat_id: UUID, body: ChatPatch, p: Who, session: Db, settings: Cfg) -> dict:
    chat = owned_chat(session, p, chat_id)
    _check_model(settings, body.model)
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
    groups: list[Path_] = Field(default_factory=list, max_length=100)


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
    groups = set(body.groups)
    session.execute(delete(ChatShare).where(ChatShare.chat_id == chat.id))
    session.add_all(ChatShare(chat_id=chat.id, principal_kind="user", principal=str(u)) for u in users)
    session.add_all(ChatShare(chat_id=chat.id, principal_kind="group", principal=g) for g in groups)
    session.commit()
    return _shares(session, chat.id)


def _withheld(session: Session, p: Principal, msgs: list[dict]) -> dict[str, str]:
    """Gate (c): doc id / note id -> label name for cited or retrieved sources the caller may not export
    (owner/admin exempt). A note counts through its own label as well as its document's."""
    items = [c for m in msgs for c in (*m["citations"], *m["retrieved"])]
    ids, nids = set(), set()
    for c in items:
        for key, into in (("doc_id", ids), ("note_id", nids)):
            try:
                into.add(UUID(str(c[key])))
            except (KeyError, ValueError):
                pass
    if not ids and not nids:
        return {}
    policy = Policy.load(session)
    out = {
        str(d.id): policy.get(d.label_id).name
        for d in session.scalars(select(Document).where(Document.id.in_(ids)))
        if policy.has(d.label_id, "block_export") and not export_exempt(p, d)
    }
    if nids:
        for n, d in session.execute(
            select(Annotation, Document)
            .join(Document, Document.id == Annotation.document_id)
            .where(Annotation.id.in_(nids), Annotation.label_id.is_not(None))
        ):
            if policy.has(n.label_id, "block_export") and not export_exempt(p, d):
                out[str(n.id)] = policy.get(n.label_id).name
    return out


def _reference(c: dict, withheld: dict[str, str]) -> str:
    if not c.get("available"):
        return f"[{c['n']}] Source not available"
    if c.get("doc_id") in withheld:
        return f"[{c['n']}] [withheld: {withheld[c['doc_id']]}]"
    parts = [", ".join(c.get("authors") or []), c.get("title") or "", str(c["year"]) if c.get("year") else ""]
    parts.append(f"p. {c['page']}" if c.get("page") else "")
    return f"[{c['n']}] " + ", ".join(x for x in parts if x)


@router.get("/chats/{chat_id}/export.md")
def export_chat(chat_id: UUID, p: Who, session: Db) -> Response:
    chat = readable_chat(session, p, chat_id)
    lines = [f"# {chat.title or 'Chat'}", ""]
    msgs = _messages(session, p, chat)
    withheld = _withheld(session, p, msgs)
    if withheld:
        audit(session, p, "label.export_blocked", str(chat.id), {"route": "chat.export"})
    for m in msgs:
        content = m["content"]
        if m["role"] == "assistant":  # answer text derived from export-blocked docs is withheld too
            keys = [str(c.get(k)) for c in (*m["citations"], *m["retrieved"]) for k in ("doc_id", "note_id")]
            hit = sorted(k for k in keys if k in withheld)
            if hit:
                content = withheld_text(withheld[hit[0]])
        lines += [f"**{'You' if m['role'] == 'user' else 'rOracle'}:** {content}", ""]
        if m["role"] == "assistant" and m["citations"]:
            lines += ["References", "", *(_reference(c, withheld) for c in m["citations"]), ""]
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
        row.value, row.comment, row.created_at = body.value, body.comment, func.now()
    session.commit()
    return {"value": body.value, "comment": body.comment}


@router.get("/admin/ratings")
def admin_ratings(p: Admin, session: Db, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict]:
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
