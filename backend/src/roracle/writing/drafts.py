"""Drafts: a `documents` row without versions + `drafts` side table + immutable `draft_versions`.

Access rule (NOT visible_documents): admin, owner, or co-author (user id / group). Everything else, and any
soft-deleted document, is 404. One helper (`readable_draft`) guards every route and MCP tool."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from roracle.access import group_path, org_units
from roracle.audit import audit
from roracle.auth import MANAGE, Principal, require_scope
from roracle.labels.policy import Policy, label_json
from roracle.models import Document, DocumentShare, User
from roracle.models_writing import Draft, DraftVersion
from roracle.writing import profiles


def _can_read(p: Principal, doc: Document, d: Draft) -> bool:
    if p.is_admin or doc.owner_id == p.user_id:
        return True
    co = d.coauthors or {}
    return str(p.user_id) in co.get("users", []) or bool(
        set(org_units(list(p.groups))) & set(co.get("groups", []))
    )


def _agent_blocked(session: Session, p: Principal, doc: Document) -> bool:
    """Gate (b): an agent/PAT principal sees a draft labelled `block_agents` as non-existent."""
    return p.is_agent and Policy.load(session).has(doc.label_id, "block_agents")


def readable_draft(
    session: Session, p: Principal, doc_id: UUID, lock: bool = False
) -> tuple[Document, Draft]:
    """404 unless the caller may read/save. `lock` takes the drafts row FOR UPDATE (access re-checked on it)."""
    q = select(Draft).where(Draft.document_id == doc_id)
    d = session.scalar(q.with_for_update() if lock else q)
    doc = session.get(Document, doc_id) if d else None
    if (
        d is None
        or doc is None
        or doc.deleted_at is not None
        or not _can_read(p, doc, d)
        or _agent_blocked(session, p, doc)
    ):
        raise HTTPException(404, "not found")
    return doc, d


def owned_draft(session: Session, p: Principal, doc_id: UUID, lock: bool = False) -> tuple[Document, Draft]:
    """404 if unreadable, 403 if readable but not the owner (admins are not owners: owner-only actions)."""
    doc, d = readable_draft(session, p, doc_id, lock)
    if doc.owner_id != p.user_id:
        raise HTTPException(403, "only the draft owner can do this")
    return doc, d


def owned_or_coauthored(
    session: Session, p: Principal, doc_id: UUID, lock: bool = False
) -> tuple[Document, Draft]:
    """404 if unreadable; 403 unless owner or co-author (admins read, but never author checks/acks)."""
    doc, d = readable_draft(session, p, doc_id, lock)
    if not _can_read(replace(p, is_admin=False), doc, d):
        raise HTTPException(403, "only the authors can do this")
    return doc, d


def _check_size(content: str, max_bytes: int) -> None:
    if len(content.encode()) > max_bytes:
        raise HTTPException(413, "draft too large")


def _touch(d: Draft) -> None:
    d.updated_at = datetime.now(UTC)


def create(
    session: Session, p: Principal, title: str, content: str | None, profile: str | None, max_bytes: int
) -> Draft:
    units = org_units(list(p.groups))
    org = units[0] if units else "/"
    try:
        prof, _, _ = profiles.resolve(session, profile, org, None)
    except KeyError:
        raise HTTPException(422, "unknown profile") from None
    text = prof.template if content is None else content
    _check_size(text, max_bytes)
    doc = Document(owner_id=p.user_id, title=title, org_unit=org, visibility="private")
    session.add(doc)
    session.flush()
    now = datetime.now(UTC)
    d = Draft(document_id=doc.id, profile=profile or None, created_at=now, updated_at=now)
    session.add(d)
    session.flush()
    session.add(DraftVersion(document_id=doc.id, version=1, content=text, author_id=p.user_id))
    audit(session, p, "draft.create", str(doc.id))
    session.flush()
    return d


def profile_for(session: Session, doc: Document, d: Draft, meta: dict) -> tuple[profiles.Profile, int, str]:
    """The draft's profile; a removed stored profile falls back to R4 selection."""
    t = meta.get("doc_type") if isinstance(meta.get("doc_type"), str) else None
    try:
        return profiles.resolve(session, d.profile, doc.org_unit, t)
    except KeyError:
        return profiles.resolve(session, None, doc.org_unit, t)


def latest_version(session: Session, doc_id: UUID) -> DraftVersion | None:
    return session.scalar(
        select(DraftVersion).where(DraftVersion.document_id == doc_id).order_by(DraftVersion.version.desc())
    )


def save(
    session: Session, p: Principal, doc_id: UUID, content: str, base_version: int | None, max_bytes: int
) -> DraftVersion:
    _, d = readable_draft(session, p, doc_id, lock=True)
    _check_size(content, max_bytes)
    if d.status == "in_review":
        raise HTTPException(409, "draft is in review")
    cur = (
        session.scalar(select(func.max(DraftVersion.version)).where(DraftVersion.document_id == doc_id)) or 0
    )
    if base_version is not None and base_version != cur:
        raise HTTPException(409, {"code": "draft_changed", "version": cur})
    v = DraftVersion(document_id=doc_id, version=cur + 1, content=content, author_id=p.user_id)
    session.add(v)
    _touch(d)
    audit(session, p, "draft.save", str(doc_id), {"version": v.version})
    session.flush()
    return v


def set_coauthors(
    session: Session, p: Principal, doc_id: UUID, users: list[UUID], groups: list[str]
) -> Draft:
    doc, d = owned_draft(session, p, doc_id, lock=True)
    ids = {u for u in users if u != doc.owner_id}
    found = set(session.scalars(select(User.id).where(User.id.in_(ids)))) if ids else set()
    if found != ids:
        raise HTTPException(422, "unknown user")
    try:
        paths = sorted({group_path(g) for g in groups})
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    uids = sorted(str(u) for u in ids)
    d.coauthors = {"users": uids, "groups": paths}
    if d.status != "submitted":  # afterwards the published document's own sharing rules apply
        session.execute(delete(DocumentShare).where(DocumentShare.document_id == doc_id))
        session.add_all(DocumentShare(document_id=doc_id, principal_kind="user", principal=u) for u in uids)
        session.add_all(DocumentShare(document_id=doc_id, principal_kind="group", principal=g) for g in paths)
        doc.visibility = "shared" if uids or paths else "private"
    _touch(d)
    audit(session, p, "draft.coauthors", str(doc_id), {"users": len(uids), "groups": len(paths)})
    session.flush()
    return d


def delete_draft(session: Session, p: Principal, doc_id: UUID) -> None:
    doc, d = owned_draft(session, p, doc_id, lock=True)
    if d.submitted_version is not None:  # a publication exists: same bar as deleting the paper
        require_scope(session, p, MANAGE)
    if d.status == "submitted":
        raise HTTPException(409, "draft already submitted")
    doc.deleted_at = datetime.now(UTC)
    audit(session, p, "draft.delete", str(doc_id))
    session.flush()


def who(session: Session, uid: UUID) -> dict:
    u = session.get(User, uid)
    return {"id": str(uid), "username": u.username if u else None}


def summary(
    session: Session, d: Draft, doc: Document, latest: int | None = None, policy: Policy | None = None
) -> dict:
    """`latest`: the draft's newest version number when the caller already knows it (saves a query);
    `policy`: a loaded label policy, to share one load across a list."""
    if latest is None:
        latest = session.scalar(
            select(func.max(DraftVersion.version)).where(DraftVersion.document_id == doc.id)
        )
    return {
        "id": str(doc.id),
        "title": doc.title,
        "profile": d.profile,
        "status": d.status,
        "owner": who(session, doc.owner_id),
        "coauthors": d.coauthors,
        "latest_version": latest or 0,
        "updated_at": d.updated_at,
        "review_note": d.review_note,
        "label": label_json((policy or Policy.load(session)).get(doc.label_id)),
    }
