"""Community notes attached to already-ACL-checked documents (R5, R8): search hits and chat passages."""

from uuid import UUID

from sqlalchemy import Integer, and_, case, cast, exists, false, func, or_, select
from sqlalchemy.orm import Session

from roracle.auth import Principal
from roracle.models import Annotation, AnnotationReport, AnnotationVote, DocumentVersion, User
from roracle.notes.access import readable_notes
from roracle.retrieval import Passage

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


def strict_gate(settings) -> tuple[int, float]:
    """LLM-bound notes: another user's note needs this many upvotes and a ratio above this (prompt-injection gate)."""
    return settings.chat_note_min_upvotes, settings.chat_note_min_ratio


def community_notes(
    session: Session,
    p: Principal,
    doc_pages: dict[str, set[int]],
    body_max: int = BODY_MAX,
    strict: tuple[int, float] | None = None,
) -> dict[str, list[dict]]:
    """doc id -> up to 2 eligible notes (<= 6 overall); notes anchored on a shown page rank first.

    `strict=(min_upvotes, min_ratio)` is the gate for notes that reach an LLM: the caller's own notes
    pass; others need the upvotes and a ratio above the floor. Loading is bounded in SQL."""
    docs = _uuids(doc_pages)
    if not docs:
        return {}
    A = Annotation
    up = func.coalesce(func.sum(case((AnnotationVote.value == 1, 1), else_=0)), 0)
    down = func.coalesce(func.sum(case((AnnotationVote.value == -1, 1), else_=0)), 0)
    votes = (
        select(AnnotationVote.annotation_id.label("aid"), up.label("up"), down.label("down"))
        .join(Annotation, Annotation.id == AnnotationVote.annotation_id)
        .where(AnnotationVote.user_id != Annotation.author_id)  # self-votes never count
        .group_by(AnnotationVote.annotation_id)
        .subquery()
    )
    reports = (
        select(AnnotationReport.annotation_id.label("aid"), func.count().label("n"))
        .group_by(AnnotationReport.annotation_id)
        .subquery()
    )
    nup, ndown = func.coalesce(votes.c.up, 0), func.coalesce(votes.c.down, 0)
    score = (nup + 1.0) / (nup + ndown + 2.0)
    page = cast(A.anchor["page"].astext, Integer)
    on_page = or_(
        false(),
        *[
            and_(A.document_id == UUID(d), page.in_(sorted(pg)))
            for d, pg in doc_pages.items()
            if pg and _uuids([d])
        ],
    )
    latest = exists(
        select(1).where(
            DocumentVersion.id == A.version_id,
            DocumentVersion.is_latest.is_(True),
            DocumentVersion.stage == "searchable",
        )
    )
    mine = A.author_id == p.user_id
    if strict:
        gate = or_(mine, and_(A.visibility == "everyone", nup >= strict[0], score > strict[1]))
    else:
        gate = and_(A.visibility == "everyone", score >= MIN_SCORE)
    ranked = (
        select(
            A.id,
            A.document_id,
            A.kind,
            func.substr(A.body, 1, body_max).label("body"),
            A.author_id,
            page.label("page"),
            A.created_at,
            score.label("score"),
            on_page.label("on_page"),
            func.row_number()
            .over(partition_by=A.document_id, order_by=(on_page.desc(), score.desc(), A.created_at, A.id))
            .label("rn"),
        )
        .select_from(A)
        .outerjoin(votes, votes.c.aid == A.id)
        .outerjoin(reports, reports.c.aid == A.id)
        .where(
            A.document_id.in_(docs),
            A.parent_id.is_(None),
            A.status == "visible",
            A.stale_version.is_(None),
            latest,
            func.coalesce(reports.c.n, 0) < MAX_REPORTS,
            gate,
            readable_notes(p),
        )
        .subquery()
    )
    rows = session.execute(
        select(ranked)
        .where(ranked.c.rn <= PER_DOC)
        .order_by(ranked.c.on_page.desc(), ranked.c.score.desc(), ranked.c.created_at, ranked.c.id)
    ).all()
    if not rows:
        return {}
    names = dict(
        session.execute(select(User.id, User.username).where(User.id.in_({r.author_id for r in rows}))).all()
    )
    out: dict[str, list[dict]] = {}
    for r in rows[:TOTAL]:
        out.setdefault(str(r.document_id), []).append(
            {
                "id": str(r.id), "kind": r.kind, "body": r.body,
                "author": {"id": str(r.author_id), "username": names.get(r.author_id, "")},
                "score": float(r.score), "page": r.page,
            }
        )  # fmt: skip
    return out


def attach_notes(
    session: Session, p: Principal, hits: list[dict], on: bool, strict: tuple[int, float] | None = None
) -> None:
    """Set `hit["notes"]` on search hits (empty list when off)."""
    found = (
        community_notes(
            session, p, {h["doc_id"]: {x["page"] for x in h["passages"]} for h in hits}, strict=strict
        )
        if on
        else {}
    )
    for h in hits:
        h["notes"] = found.get(h["doc_id"], [])


def note_passages(session: Session, p: Principal, passages: list, strict: tuple[int, float]) -> list:
    """Chat passages for community notes on the docs in `passages` (appended, not counted in k)."""
    first: dict[str, Passage] = {}
    pages: dict[str, set[int]] = {}
    for x in passages:
        first.setdefault(x.doc_id, x)
        pages.setdefault(x.doc_id, set()).update(x.pages)
    out = []
    for doc, items in community_notes(session, p, pages, 1200, strict).items():
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
                    authors=[n["author"]["username"]],
                    note_id=n["id"],
                )
            )
    return out
