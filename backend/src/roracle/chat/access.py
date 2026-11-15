"""Chat access: readable (owner or share recipient) vs owned; per-reader citation re-check."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from roracle.access import visible_documents
from roracle.auth import Principal
from roracle.models import Chat, ChatShare, Document, DocumentVersion


def _readable(session: Session, p: Principal, chat: Chat) -> bool:
    if chat.owner_id == p.user_id:
        return True
    conds = [(ChatShare.principal_kind == "user") & (ChatShare.principal == str(p.user_id))]
    if p.groups:
        conds.append((ChatShare.principal_kind == "group") & (ChatShare.principal.in_(list(p.groups))))
    return bool(session.scalar(select(exists().where(ChatShare.chat_id == chat.id, or_(*conds)))))


def readable_chat(session: Session, p: Principal, chat_id: UUID) -> Chat:
    chat = session.get(Chat, chat_id)
    if chat is None or not _readable(session, p, chat):
        raise HTTPException(404, "not found")
    return chat


def owned_chat(session: Session, p: Principal, chat_id: UUID) -> Chat:
    chat = readable_chat(session, p, chat_id)
    if chat.owner_id != p.user_id:
        raise HTTPException(403, "read-only")
    return chat


def visible_versions(session: Session, p: Principal, items: list[dict]) -> set[tuple[str, str]]:
    """(doc_id, version_id) pairs among `items` the caller may still see (one query)."""
    docs: set[UUID] = set()
    vers: set[UUID] = set()
    for x in items:
        try:
            docs.add(UUID(str(x.get("doc_id"))))
            vers.add(UUID(str(x.get("version_id"))))
        except ValueError:
            continue
    if not docs:
        return set()
    rows = session.execute(
        select(Document.id, DocumentVersion.id)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(Document.id.in_(docs), DocumentVersion.id.in_(vers), visible_documents(p))
    )
    return {(str(d), str(v)) for d, v in rows}


def recheck(items: list[dict], ok: set[tuple[str, str]], *, drop: bool) -> list[dict]:
    """Visible -> item + available:true; invisible -> {"n", "available": false} (or dropped)."""
    out = []
    for x in items:
        if (x.get("doc_id"), x.get("version_id")) in ok:
            out.append({**x, "available": True})
        elif not drop:
            out.append({"n": x.get("n"), "available": False})
    return out
