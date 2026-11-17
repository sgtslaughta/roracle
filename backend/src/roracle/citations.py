"""Link a version's parsed references to library documents; ACL is applied at READ time only."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from rapidfuzz import fuzz, utils
from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session, aliased

from roracle.access import visible_documents
from roracle.auth import Principal
from roracle.identifiers import MATCHABLE, add_id
from roracle.models import DocCitation, Document, DocumentVersion
from roracle.queue import payload_uuid

FUZZY_MIN = 92


@dataclass(frozen=True)
class Cand:
    id: UUID
    ids: frozenset[tuple[str, str]]
    title: str
    year: int | None
    norm: str = ""


def match_ids(rec: dict | None) -> frozenset[tuple[str, str]]:
    """(scheme, value) pairs of a meta/reference dict: `ids` plus the legacy `doi` key. Journal-level ids (ISSN)
    are never matchable: they would link unrelated papers."""
    rec = rec or {}
    ids: dict[str, str] = {}
    raw = rec.get("ids") if isinstance(rec.get("ids"), dict) else {}
    for k in MATCHABLE:
        add_id(ids, k, raw.get(k))
    add_id(ids, "doi", rec.get("doi"))  # also derives arxiv/acl from 10.48550 / 10.18653 DOIs
    return frozenset((k, v) for k, v in ids.items() if k in MATCHABLE)


def _year(y) -> int | None:
    try:
        return int(float(str(y).strip()))
    except (TypeError, ValueError, OverflowError):
        return None


def _tokens_ok(norm: str) -> bool:
    """Short/generic titles ("Introduction") must never fuzzy-match."""
    return len(norm.split()) >= 3 and len(norm) >= 15


def _cand(doc_id: UUID, title: str | None, meta: dict | None) -> Cand:
    m = meta or {}
    t = (title or m.get("title") or "").strip()
    return Cand(doc_id, match_ids(m), t, _year(m.get("year")), utils.default_process(t))


def _candidates(session: Session, *, exclude: UUID | None = None, only: UUID | None = None) -> list[Cand]:
    stmt = (
        select(Document.id, Document.title, DocumentVersion.meta)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(
            Document.deleted_at.is_(None),
            DocumentVersion.is_latest.is_(True),
            DocumentVersion.stage == "searchable",
        )
        .order_by(Document.created_at, Document.id)  # deterministic: oldest document wins identifier clashes
    )
    if exclude:
        stmt = stmt.where(Document.id != exclude)
    if only:
        stmt = stmt.where(Document.id == only)
    return [_cand(i, t, m) for i, t, m in session.execute(stmt)]


class _Index:
    """# ponytail: in-memory scan, fine to ~100k docs; beyond that use a pg_trgm/ES candidate query.
    Known limits (accepted): a concurrent resolve (A) vs backfill (B) can miss a link, and a version
    flip briefly dips cited-by counts; upgrade = a periodic reconcile pass re-running resolve."""

    def __init__(self, cands: list[Cand]):
        self.by_id: dict[tuple[str, str], list[Cand]] = {}
        for c in cands:
            for k in c.ids:
                self.by_id.setdefault(k, []).append(c)
        self.by_year: dict[int, list[Cand]] = {}
        for c in cands:
            if c.year is not None and _tokens_ok(c.norm):
                self.by_year.setdefault(c.year, []).append(c)

    def match_all(self, ref: dict) -> dict[UUID, float]:
        """Every candidate the reference may denote (R18): identifier hits score 1.0, fuzzy hits ratio/100."""
        out: dict[UUID, float] = {}
        for k in match_ids(ref):
            for c in self.by_id.get(k, ()):
                out[c.id] = 1.0
        title, year = utils.default_process(str(ref.get("title") or "")), _year(ref.get("year"))
        if year is not None and _tokens_ok(title):
            for c in self.by_year.get(year, ()):
                r = fuzz.token_sort_ratio(title, c.norm)
                if r >= FUZZY_MIN:
                    out[c.id] = max(out.get(c.id, 0.0), r / 100)
        return out


def resolve_references(session: Session, version: DocumentVersion) -> int:
    """Replace the version's citation rows (idempotent); returns the number of references with a candidate.

    Matching is global: ALL candidates are stored; the viewer's best visible one is chosen at read time."""
    index = _Index(_candidates(session, exclude=version.document_id))
    session.execute(delete(DocCitation).where(DocCitation.src_version_id == version.id))
    matched = 0
    for n, ref in enumerate(version.references or [], 1):
        hits = index.match_all(ref if isinstance(ref, dict) else {})
        matched += bool(hits)
        session.add_all(
            DocCitation(src_version_id=version.id, ref_no=n, target_document_id=t, confidence=c)
            for t, c in hits.items()
        )
    session.flush()
    return matched


def backfill_for(session: Session, document: Document) -> int:
    """Rebuild the edges INTO `document` from other documents' latest searchable versions."""
    index = _Index(_candidates(session, only=document.id))
    session.execute(delete(DocCitation).where(DocCitation.target_document_id == document.id))
    if not index.by_id and not index.by_year:
        return 0
    versions = session.scalars(
        select(DocumentVersion).where(
            DocumentVersion.is_latest.is_(True),
            DocumentVersion.stage == "searchable",
            DocumentVersion.document_id != document.id,
            DocumentVersion.references != [],
        )
    )
    n = 0
    for v in versions:
        for no, ref in enumerate(v.references or [], 1):
            c = index.match_all(ref if isinstance(ref, dict) else {}).get(document.id)
            if c is not None:
                session.add(
                    DocCitation(src_version_id=v.id, ref_no=no, target_document_id=document.id, confidence=c)
                )
                n += 1
    session.flush()
    return n


def handle_citations(session: Session, payload: dict) -> None:
    """Job `citations` {version_id}: runs inside run_once's SAVEPOINT; never commits (R6/R9)."""
    v = session.get(DocumentVersion, payload_uuid(payload, "version_id"))
    if v is None or v.stage != "searchable":
        return
    d = session.get(Document, v.document_id)
    if d is None or d.deleted_at is not None:
        return
    resolve_references(session, v)
    backfill_for(session, d)


def best_targets(p: Principal, targets: Sequence[UUID] | None = None, sources: Sequence[UUID] | None = None):
    """Per (src_version, ref_no) the viewer's best VISIBLE candidate (rn == 1): max confidence, then
    oldest document, then id. Never refuses a tie and never reveals a candidate the viewer can't see.

    `targets` / `sources` scope the ranking to the (src_version, ref_no) groups that cite one of those documents /
    come from those versions, BEFORE the window: rows of every candidate of a kept group are still ranked
    together (a group's winner may be a non-target), but unrelated groups are never read. Without either the
    window runs over the whole table (O(corpus)); callers on a hot path must pass one."""
    tv = aliased(DocumentVersion)
    rn = func.row_number().over(
        partition_by=[DocCitation.src_version_id, DocCitation.ref_no],
        order_by=[DocCitation.confidence.desc(), Document.created_at, Document.id],
    )
    q = select(
        DocCitation.src_version_id, DocCitation.ref_no, DocCitation.target_document_id, rn.label("rn")
    ).join(Document, Document.id == DocCitation.target_document_id)
    if sources is not None:
        q = q.where(DocCitation.src_version_id.in_(list(sources)))
    if targets is not None:
        groups = (
            select(DocCitation.src_version_id, DocCitation.ref_no)
            .where(DocCitation.target_document_id.in_(list(targets)))
            .distinct()
            .subquery("grp")
        )
        q = q.join(
            groups,
            and_(
                groups.c.src_version_id == DocCitation.src_version_id,
                groups.c.ref_no == DocCitation.ref_no,
            ),
        )
    return (
        q.join(tv, and_(tv.document_id == Document.id, tv.is_latest.is_(True), tv.stage == "searchable"))
        .where(visible_documents(p))
        .subquery()
    )


def cited_by_counts_stmt(p: Principal, doc_ids: Sequence[UUID]):
    bt = best_targets(p, targets=doc_ids)
    return (
        select(bt.c.target_document_id, func.count(func.distinct(Document.id)))
        .join(DocumentVersion, DocumentVersion.id == bt.c.src_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            bt.c.rn == 1,
            bt.c.target_document_id.in_(list(doc_ids)),
            DocumentVersion.is_latest.is_(True),
            DocumentVersion.stage == "searchable",
            visible_documents(p),
        )
        .group_by(bt.c.target_document_id)
    )


def cited_by_counts(session: Session, p: Principal, doc_ids: list[UUID]) -> dict[str, int]:
    """Distinct VISIBLE citing documents (latest searchable version) per target; one query scoped to the ids."""
    if not doc_ids:
        return {}
    return {str(t): n for t, n in session.execute(cited_by_counts_stmt(p, doc_ids)).all()}
