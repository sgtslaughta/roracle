"""Community notes attached to already-ACL-checked documents (R5, R8): search hits and chat passages."""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from roracle.auth import Principal
from roracle.models import Annotation, AnnotationReport, AnnotationVote, User
from roracle.notes.access import readable_notes
from roracle.retrieval import Passage, vote_ratio

MIN_SCORE, MAX_REPORTS = 0.4, 3
PER_DOC, TOTAL, BODY_MAX = 2, 6, 500


def _uuids(xs) -> list[UUID]:
    out = []
    for x in xs:
        try:
            out.append(UUID(str(x)))
        except ValueError:
            pass
    return out


def readable_note_ids(session: Session, p: Principal, ids) -> set[str]:
    """Subset of `ids` the caller may read (one query); junk ids are dropped."""
    u = _uuids(ids)
    if not u:
        return set()
    rows = session.scalars(select(Annotation.id).where(Annotation.id.in_(u), readable_notes(p)))
    return {str(i) for i in rows}


def community_notes(
    session: Session, p: Principal, doc_pages: dict[str, set[int]], body_max: int = BODY_MAX
) -> dict[str, list[dict]]:
    """doc id -> up to 2 eligible notes (<= 6 overall); notes anchored on a shown page rank first."""
    docs = _uuids(doc_pages)
    if not docs:
        return {}
    notes = list(
        session.scalars(
            select(Annotation)
            .where(
                Annotation.document_id.in_(docs),
                Annotation.parent_id.is_(None),
                Annotation.visibility == "everyone",
                Annotation.status == "visible",
                Annotation.stale_version.is_(None),
                readable_notes(p),
            )
            .order_by(Annotation.created_at, Annotation.id)
        )
    )
    if not notes:
        return {}
    ids = [n.id for n in notes]
    counts: dict[UUID, list[int]] = defaultdict(lambda: [0, 0])
    for aid, val, c in session.execute(
        select(AnnotationVote.annotation_id, AnnotationVote.value, func.count())
        .where(AnnotationVote.annotation_id.in_(ids))
        .group_by(AnnotationVote.annotation_id, AnnotationVote.value)
    ):
        counts[aid][0 if val == 1 else 1] = c
    reports = dict(
        session.execute(
            select(AnnotationReport.annotation_id, func.count())
            .where(AnnotationReport.annotation_id.in_(ids))
            .group_by(AnnotationReport.annotation_id)
        ).all()
    )
    names = dict(
        session.execute(select(User.id, User.username).where(User.id.in_({n.author_id for n in notes}))).all()
    )
    ranked = []
    for n in notes:
        up, down = counts[n.id]
        score = vote_ratio(up, down)
        if score < MIN_SCORE or reports.get(n.id, 0) >= MAX_REPORTS:
            continue
        page = (n.anchor or {}).get("page")
        on_page = page in doc_pages.get(str(n.document_id), ())
        item = {
            "id": str(n.id), "kind": n.kind, "body": n.body[:body_max],
            "author": names.get(n.author_id, ""), "score": score, "page": page,
        }  # fmt: skip
        ranked.append((not on_page, -score, n.created_at, str(n.document_id), item))
    ranked.sort(key=lambda r: r[:3])
    out: dict[str, list[dict]] = {}
    total = 0
    for *_, doc, item in ranked:
        if total >= TOTAL:
            break
        if len(out.setdefault(doc, [])) < PER_DOC:
            out[doc].append(item)
            total += 1
    return {d: v for d, v in out.items() if v}


def attach_notes(session: Session, p: Principal, hits: list[dict], on: bool) -> None:
    """Set `hit["notes"]` on search hits (empty list when off)."""
    found = (
        community_notes(session, p, {h["doc_id"]: {x["page"] for x in h["passages"]} for h in hits})
        if on
        else {}
    )
    for h in hits:
        h["notes"] = found.get(h["doc_id"], [])


def note_passages(session: Session, p: Principal, passages: list) -> list:
    """Chat passages for community notes on the docs in `passages` (appended, not counted in k)."""
    first: dict[str, Passage] = {}
    pages: dict[str, set[int]] = {}
    for x in passages:
        first.setdefault(x.doc_id, x)
        pages.setdefault(x.doc_id, set()).update(x.pages)
    out = []
    for doc, items in community_notes(session, p, pages, 1200).items():
        x = first[doc]
        for n in items:
            out.append(
                Passage(
                    chunk_id=f"note:{n['id']}",
                    doc_id=doc,
                    version_id=x.version_id,
                    text=n["body"],
                    highlight="",
                    pages=[n["page"]] if n["page"] else [],
                    bboxes=[],
                    score=0.0,
                    title=f"Community note on: {x.title}",
                    note_id=n["id"],
                )
            )
    return out
