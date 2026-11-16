"""Handoffs: frozen, shareable context bundles. Redacted per loader at read time (no hidden id/title/passage)."""

import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from roracle.access import agent_visible, org_units, readable_documents
from roracle.audit import audit
from roracle.auth import Principal
from roracle.chat.access import readable_chat, recheck, visible_versions
from roracle.labels.textgate import doc_ids_of, gated_docs, strip_text, withhold_messages
from roracle.models import ChatMessage, Collection, CollectionItem, Document, Handoff, HandoffShare, User

# R9: note citations fail closed in handoffs (recheck notes=frozenset()).
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
PAGE = 20
MAX_NOTES = 200
KINDS = ("chat", "collection", "search", "document", "agent")
_CODE = re.compile(f"[{ALPHABET}]{{8}}")
NF = HTTPException(404, "not found")


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


def normalize(s: str) -> str:
    """Any accepted form -> canonical code. Invalid -> 404 (never 422: no oracle)."""
    t = re.split(r"[?#]", s, maxsplit=1)[0].strip().rstrip("/").rsplit("/", 1)[-1]
    t = re.sub(r"[\s-]", "", t)
    if not t.isascii() or not _CODE.fullmatch(t.upper()):
        raise HTTPException(404, "not found")
    return t.upper()


def display(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _visible_doc_ids(session: Session, p: Principal, ids) -> list[str]:
    """`ids` (any order, dupes ok) filtered to documents p can see; input order kept. Gate (b) applies to every loader."""
    uuids = []
    for i in ids:
        try:
            uuids.append(UUID(str(i)))
        except ValueError:
            continue
    ok = {
        str(x)
        for x in session.scalars(
            select(Document.id).where(Document.id.in_(uuids), readable_documents(p), agent_visible())
        )
    }
    return list(dict.fromkeys(str(u) for u in uuids if str(u) in ok))


def _chat_snapshot(session: Session, p: Principal, chat_id: UUID):
    chat = readable_chat(session, p, chat_id)
    if chat.owner_id != p.user_id:  # owner only: share recipients get 404, not 403
        raise NF
    msgs = list(
        session.scalars(
            select(ChatMessage)
            .where(ChatMessage.chat_id == chat.id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        )
    )
    cites = [c for m in msgs for c in m.citations if isinstance(c, dict)]
    ok = visible_versions(session, p, cites, agent_gate=True)
    transcript = [
        {
            "role": m.role,
            "content": m.content,
            "citations": recheck(
                [c for c in m.citations if isinstance(c, dict)], ok, drop=False, notes=frozenset()
            ),
            "created_at": _iso(m.created_at),
            "doc_ids": sorted(doc_ids_of((*m.citations, *m.retrieved))),  # label withholding at read time
        }
        for m in msgs
    ]
    seen, passages = set(), []
    for c in recheck(cites, ok, drop=True, notes=frozenset()):
        key = (c.get("doc_id"), c.get("version_id"), c.get("chunk_id"))
        if key not in seen:
            seen.add(key)
            passages.append(c)
    return chat, transcript, passages


def _put_shares(session: Session, code: str, audience: str, shares) -> None:
    for s in shares:
        if s.get("kind") not in ("user", "group") or not str(s.get("principal", "")).strip():
            raise HTTPException(422, "invalid share")
    if audience == "shared" and not shares:
        raise HTTPException(422, "shared audience needs at least one share")
    for k, pr in dict.fromkeys((s["kind"], str(s["principal"]).strip()) for s in shares):
        session.add(HandoffShare(handoff_code=code, principal_kind=k, principal=pr))


def _check(audience: str, days: int) -> None:
    if audience not in ("creator", "shared", "authenticated"):
        raise HTTPException(422, "invalid audience")
    if not 1 <= days <= 90:
        raise HTTPException(422, "expires_in_days must be 1-90")


def create(
    session: Session,
    p: Principal,
    kind: str,
    *,
    source_id=None,
    title: str = "",
    goal: str = "",
    brief: str = "",
    doc_ids=(),
    audience: str = "creator",
    shares=(),
    expires_in_days: int = 7,
    via: str = "ui",
) -> Handoff:
    if kind not in KINDS:
        raise HTTPException(422, "invalid kind")
    _check(audience, expires_in_days)
    source: dict = {}
    transcript: list = []
    passages: list = []
    ids = list(doc_ids)
    if kind == "chat":
        chat, transcript, passages = _chat_snapshot(session, p, _uuid(source_id))
        title = title or chat.title
        source = {"chat_id": str(chat.id)}
        ids += [c["doc_id"] for c in passages]
    elif kind == "collection":
        col = session.get(Collection, _uuid(source_id))
        if col is None or col.owner_id != p.user_id:
            raise NF
        title = title or col.name
        source = {"collection_id": str(col.id), "name": col.name}
        ids += [
            str(d)
            for d in session.scalars(
                select(CollectionItem.document_id).where(CollectionItem.collection_id == col.id)
            )
        ]
    elif kind == "search":
        source = {"query": goal or title}
    elif kind == "document":
        doc = _visible_doc_ids(session, p, [source_id])
        if not doc:
            raise NF
        source = {"doc_id": doc[0]}
        ids = [doc[0], *ids]
    ids = _visible_doc_ids(session, p, ids)
    for _ in range(5):
        h = Handoff(
            code=new_code(), creator_id=p.user_id, kind=kind, title=title, goal=goal, brief=brief,
            source=source, doc_ids=ids, passages=passages, transcript=transcript, notes=[],
            audience=audience, created_via=via, expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
        )  # fmt: skip
        try:
            with session.begin_nested():
                session.add(h)
                session.flush()
        except IntegrityError as e:
            if getattr(e.orig.diag, "constraint_name", None) != "handoffs_pkey":
                raise
            continue
        break
    else:
        raise HTTPException(500, "could not allocate handoff code")
    _put_shares(session, h.code, audience, shares if audience == "shared" else [])
    audit(session, p, "handoff.create", h.code, {"kind": kind})
    session.flush()
    return h


def _uuid(v) -> UUID:
    try:
        return UUID(str(v))
    except ValueError:
        raise NF from None


def _in_audience(session: Session, p: Principal, h: Handoff) -> bool:
    if h.creator_id == p.user_id or h.audience == "authenticated":
        return True
    if h.audience != "shared":
        return False
    conds = [(HandoffShare.principal_kind == "user") & (HandoffShare.principal == str(p.user_id))]
    if p.groups:
        conds.append(
            (HandoffShare.principal_kind == "group") & (HandoffShare.principal.in_(org_units(p.groups)))
        )
    q = select(HandoffShare.principal).where(
        HandoffShare.handoff_code == h.code, conds[0] if len(conds) == 1 else conds[0] | conds[1]
    )
    return session.scalar(q.limit(1)) is not None


def _audience_get(session: Session, p: Principal, code: str) -> Handoff:
    h = session.get(Handoff, normalize(code))
    if h is None or not _in_audience(session, p, h):
        raise NF
    return h


def _loadable(session: Session, p: Principal, code: str) -> Handoff:
    h = _audience_get(session, p, code)
    if h.revoked_at is not None:
        raise HTTPException(410, "handoff revoked")
    if h.expires_at <= datetime.now(UTC):
        raise HTTPException(410, "handoff expired")
    return h


def view(session: Session, p: Principal, h: Handoff) -> dict:
    """Manifest for p, no audit. Redacted: hidden docs/passages dropped and only counted in `withheld`."""
    shown = _visible_doc_ids(session, p, h.doc_ids)
    ok = visible_versions(session, p, h.passages, agent_gate=True)
    passages = recheck(h.passages, ok, drop=True, notes=frozenset())
    passages = strip_text(passages, gated_docs(session, p, doc_ids_of(passages)))
    every = set(map(str, h.doc_ids)) | {str(x.get("doc_id")) for x in h.passages}
    mine = h.creator_id == p.user_id
    src = h.source
    if h.kind == "document":
        src = src if src.get("doc_id") in shown else {}
    elif h.kind in ("chat", "collection") and not mine:
        src = {}  # creator's private handles
    withheld = len(every - set(shown) - {x["doc_id"] for x in passages})
    author = session.get(User, h.creator_id)
    titles = dict(
        session.execute(
            select(Document.id, Document.title).where(Document.id.in_([UUID(d) for d in shown]))
        ).all()
    )
    out = {
        "code": h.code, "display": display(h.code), "url": "/h/" + display(h.code),
        "uri": "roracle://handoff/" + h.code, "kind": h.kind, "title": h.title, "goal": h.goal,
        "brief": h.brief, "source": src, "doc_ids": shown,
        "documents": [{"id": d, "title": titles.get(UUID(d))} for d in shown], "passages": passages,
        "notes": h.notes[-PAGE:], "withheld": withheld,
        "parts": {"transcript": ceil(len(h.transcript) / PAGE), "notes": ceil(len(h.notes) / PAGE)},
        "audience": h.audience, "expires_at": _iso(h.expires_at), "created_at": _iso(h.created_at),
        "created_by": author.username if author else "", "created_via": h.created_via,
    }  # fmt: skip
    if mine or p.is_admin:  # recipients never see who else it is shared with
        out["shares"] = [
            {"kind": s.principal_kind, "principal": s.principal}
            for s in session.scalars(
                select(HandoffShare)
                .where(HandoffShare.handoff_code == h.code)
                .order_by(HandoffShare.principal_kind, HandoffShare.principal)
            )
        ]
    return out


def absolute(m: dict, base: str) -> dict:
    """`url` made absolute when a public base URL is configured."""
    base = base.rstrip("/")
    return {**m, "url": base + m["url"]} if base else m


def load(session: Session, p: Principal, code: str) -> dict:
    h = _loadable(session, p, code)
    audit(session, p, "handoff.load", h.code)
    return view(session, p, h)


def get_part(session: Session, p: Principal, code: str, part: str, page: int = 1) -> dict:
    h = _loadable(session, p, code)
    if part not in ("transcript", "notes"):
        raise NF
    rows = h.transcript if part == "transcript" else h.notes
    pages = ceil(len(rows) / PAGE)
    items = rows[(page - 1) * PAGE : page * PAGE]
    if part == "transcript":
        cites = [c for m in items for c in m.get("citations", [])]
        ok = visible_versions(session, p, cites, agent_gate=True)
        gated = gated_docs(
            session, p, {d for m in items for d in (*m.get("doc_ids", []), *doc_ids_of(cites))}
        )
        items = [
            {
                **{k: v for k, v in m.items() if k != "doc_ids"},
                "citations": strip_text(
                    recheck(m.get("citations", []), ok, drop=False, notes=frozenset()), gated
                ),
                "_docs": {*m.get("doc_ids", []), *doc_ids_of(m.get("citations", []))},
            }
            for m in items
        ]
        withhold_messages(items, gated, lambda m: m["_docs"])
        for m in items:
            del m["_docs"]
    audit(session, p, "handoff.load", h.code, {"part": part, "page": page})
    return {"part": part, "items": items, "page": page, "pages": pages}


def append_note(session: Session, p: Principal, code: str, text: str, via: str = "ui") -> dict:
    h = _loadable(session, p, code)
    if "write" not in p.scopes:
        raise HTTPException(403, "insufficient scope")
    if not 1 <= len(text) <= 10000:
        raise HTTPException(422, "note must be 1-10000 characters")
    note = {"at": datetime.now(UTC).isoformat(), "by": p.username, "text": text, "via": via}
    n = session.execute(
        sql_text(
            "UPDATE handoffs SET notes = notes || CAST(:note AS jsonb) "
            "WHERE code = :code AND jsonb_array_length(notes) < :cap"
        ),
        {"note": json.dumps([note]), "code": h.code, "cap": MAX_NOTES},
    ).rowcount
    if not n:
        raise HTTPException(409, "note limit reached")
    session.expire(h, ["notes"])
    audit(session, p, "handoff.note", h.code)
    return note


def _creator_get(session: Session, p: Principal, code: str) -> Handoff:
    h = _audience_get(session, p, code)
    if h.creator_id != p.user_id:
        raise HTTPException(403, "creator only")
    return h


def revoke(session: Session, p: Principal, code: str) -> None:
    h = _creator_get(session, p, code)
    if h.revoked_at is None:
        h.revoked_at = datetime.now(UTC)
        audit(session, p, "handoff.revoke", h.code)


def update(session: Session, p: Principal, code: str, **f) -> Handoff:
    """f: any of title, goal, brief, audience, shares, expires_in_days (None = unchanged)."""
    h = _creator_get(session, p, code)
    if h.revoked_at is not None:
        raise HTTPException(410, "handoff revoked")
    f = {k: v for k, v in f.items() if v is not None}
    audience = f.get("audience", h.audience)
    days = f.get("expires_in_days", 1)
    _check(audience, days)
    if f.get("shares") and audience != "shared":
        raise HTTPException(422, "shares need audience shared")
    for k in ("title", "goal", "brief"):
        if k in f:
            setattr(h, k, f[k])
    if "shares" in f or "audience" in f:
        existing = [
            {"kind": s.principal_kind, "principal": s.principal}
            for s in session.scalars(select(HandoffShare).where(HandoffShare.handoff_code == h.code))
        ]
        shares = f.get("shares", existing)
        if audience == "shared" and not shares:
            raise HTTPException(422, "shared audience needs at least one share")
        if "shares" in f or audience != "shared":
            for s in session.scalars(select(HandoffShare).where(HandoffShare.handoff_code == h.code)):
                session.delete(s)
            session.flush()
            _put_shares(session, h.code, audience, shares if audience == "shared" else [])
        h.audience = audience
    if "expires_in_days" in f:
        h.expires_at = datetime.now(UTC) + timedelta(days=f["expires_in_days"])
    audit(session, p, "handoff.update", h.code)
    session.flush()
    return h


def list_mine(session: Session, p: Principal, limit: int = 100, offset: int = 0) -> list[dict]:
    q = (
        select(Handoff)
        .where(Handoff.creator_id == p.user_id)
        .order_by(Handoff.created_at.desc(), Handoff.code)
        .limit(limit)
        .offset(offset)
    )
    return [
        {
            "code": h.code, "display": display(h.code), "url": "/h/" + display(h.code),
            "kind": h.kind, "title": h.title,
            "audience": h.audience, "expires_at": _iso(h.expires_at), "revoked_at": _iso(h.revoked_at),
            "created_at": _iso(h.created_at), "created_via": h.created_via,
        }
        for h in session.scalars(q)
    ]  # fmt: skip
