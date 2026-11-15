"""Annotation CRUD, threads and JSON (P8 task 2)."""

from collections import defaultdict
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from roracle.auth import Principal
from roracle.models import (
    Annotation,
    AnnotationShare,
    AnnotationVote,
    Document,
    DocumentVersion,
    Evidence,
    User,
)
from roracle.notes.access import doc_readable, get_readable
from roracle.notes.schemas import NoteIn, NotePatch, ReplyIn, Share
from roracle.retrieval import vote_ratio

NOT_FOUND = HTTPException(404, "not found")


def _doc_version(session: Session, p: Principal, doc: Document) -> DocumentVersion:
    q = select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
    if not (p.is_admin or doc.owner_id == p.user_id):
        q = q.where(DocumentVersion.stage == "searchable")
    v = session.scalar(q.order_by(DocumentVersion.version.desc()).limit(1))
    if v is None:
        raise NOT_FOUND
    return v


def latest_searchable(session: Session, doc_id: UUID) -> DocumentVersion | None:
    return session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc_id, DocumentVersion.stage == "searchable")
        .order_by(DocumentVersion.version.desc())
        .limit(1)
    )


def _set_shares(session: Session, note: Annotation, shares: list[Share]) -> None:
    session.query(AnnotationShare).filter_by(annotation_id=note.id).delete()
    seen = {(s.kind, s.principal) for s in shares}
    session.add_all(AnnotationShare(annotation_id=note.id, principal_kind=k, principal=v) for k, v in seen)


def create(
    session: Session,
    p: Principal,
    doc_id: UUID,
    body: NoteIn,
    *,
    agent_assisted: bool = False,
    default_visibility: str = "everyone",
) -> Annotation:
    if not doc_readable(session, p, doc_id):
        raise NOT_FOUND
    if body.relation is not None:
        tid = body.relation.target_document_id
        if tid == doc_id:
            raise HTTPException(422, "relation target must differ from the document")
        if not doc_readable(session, p, tid):
            raise NOT_FOUND
    doc = session.get(Document, doc_id)
    v = _doc_version(session, p, doc)
    vis = body.visibility or default_visibility
    n = Annotation(
        document_id=doc_id,
        version_id=v.id,
        author_id=p.user_id,
        kind=body.kind,
        body=body.body,
        anchor=body.anchor.model_dump(mode="json") if body.anchor else None,
        relation_type=body.relation.type if body.relation else None,
        target_document_id=body.relation.target_document_id if body.relation else None,
        visibility=vis,
        agent_assisted=agent_assisted,
    )
    session.add(n)
    session.flush()
    if vis == "shared":
        _set_shares(session, n, body.shares)
    session.flush()
    session.refresh(n)
    return n


def reply(
    session: Session, p: Principal, parent_id: UUID, body: ReplyIn, *, agent_assisted: bool = False
) -> Annotation:
    parent = get_readable(session, p, parent_id)
    root = parent if parent.parent_id is None else session.get(Annotation, parent.parent_id)
    n = Annotation(
        document_id=root.document_id,
        version_id=root.version_id,
        parent_id=root.id,
        author_id=p.user_id,
        kind="comment",
        body=body.body,
        visibility=root.visibility,
        agent_assisted=agent_assisted,
    )
    session.add(n)
    session.flush()
    session.refresh(n)
    return n


def _own(session: Session, p: Principal, note_id: UUID) -> Annotation:
    n = get_readable(session, p, note_id)  # unreadable -> 404
    if n.author_id != p.user_id:
        raise HTTPException(403, "only the author may change this note")
    return n


def patch(session: Session, p: Principal, note_id: UUID, body: NotePatch) -> Annotation:
    n = _own(session, p, note_id)
    if n.parent_id is not None and (body.visibility is not None or body.shares is not None):
        raise HTTPException(422, "replies follow their thread's visibility")
    if body.body is not None:
        n.body = body.body
    vis = body.visibility or n.visibility
    if body.visibility is not None:
        n.visibility = vis
    if vis != "shared":
        _set_shares(session, n, [])
    elif body.shares is not None:
        _set_shares(session, n, body.shares)
    session.flush()
    session.refresh(n)
    return n


def delete(session: Session, p: Principal, note_id: UUID) -> None:
    session.delete(_own(session, p, note_id))
    session.flush()


def to_json(session: Session, p: Principal, notes: list[Annotation]) -> list[dict]:
    if not notes:
        return []
    nids = [n.id for n in notes]
    users = {u.id: u for u in session.scalars(select(User).where(User.id.in_({n.author_id for n in notes})))}
    vers = dict(
        session.execute(
            select(DocumentVersion.id, DocumentVersion.version).where(
                DocumentVersion.id.in_({n.version_id for n in notes})
            )
        ).all()
    )
    votes: dict[UUID, dict] = defaultdict(lambda: {"up": 0, "down": 0, "mine": 0})
    for aid, uid, val in session.execute(
        select(AnnotationVote.annotation_id, AnnotationVote.user_id, AnnotationVote.value).where(
            AnnotationVote.annotation_id.in_(nids)
        )
    ):
        votes[aid]["up" if val == 1 else "down"] += 1
        if uid == p.user_id:
            votes[aid]["mine"] = val
    ev: dict[UUID, dict] = defaultdict(lambda: {"supports": 0, "refutes": 0, "inconclusive": 0})
    for aid, stance, c in session.execute(
        select(Evidence.annotation_id, Evidence.stance, func.count())
        .where(Evidence.annotation_id.in_(nids))
        .group_by(Evidence.annotation_id, Evidence.stance)
    ):
        ev[aid][stance] = c
    mine = [n.id for n in notes if n.author_id == p.user_id]
    shares: dict[UUID, list] = defaultdict(list)
    if mine:
        for s in session.scalars(select(AnnotationShare).where(AnnotationShare.annotation_id.in_(mine))):
            shares[s.annotation_id].append({"kind": s.principal_kind, "principal": s.principal})
    out = []
    for n in notes:
        v = votes.get(n.id, {"up": 0, "down": 0, "mine": 0})
        u = users.get(n.author_id)
        out.append(
            {
                "id": str(n.id),
                "document_id": str(n.document_id),
                "version_id": str(n.version_id),
                "version": vers.get(n.version_id),
                "parent_id": str(n.parent_id) if n.parent_id else None,
                "author": {"id": str(n.author_id), "username": u.username if u else ""},
                "kind": n.kind,
                "body": n.body,
                "anchor": n.anchor,
                "relation": {"type": n.relation_type, "target_document_id": str(n.target_document_id)}
                if n.relation_type
                else None,
                "visibility": n.visibility,
                "shares": sorted(shares[n.id], key=lambda s: (s["kind"], s["principal"])),
                "status": n.status,
                "agent_assisted": n.agent_assisted,
                "may_not_apply_to_version": n.stale_version,
                "votes": {**v, "score": vote_ratio(v["up"], v["down"])},
                "evidence": dict(ev[n.id])
                if n.id in ev
                else {"supports": 0, "refutes": 0, "inconclusive": 0},
                "created_at": n.created_at.isoformat(),
                "updated_at": n.updated_at.isoformat(),
            }
        )
    return out
