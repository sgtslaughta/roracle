"""Link a version's parsed references to library documents; ACL is applied at READ time only."""

import re
from dataclasses import dataclass
from urllib.parse import unquote
from uuid import UUID

from rapidfuzz import fuzz, utils
from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from roracle.access import visible_documents
from roracle.auth import Principal
from roracle.models import DocCitation, Document, DocumentVersion

FUZZY_MIN = 92


@dataclass(frozen=True)
class Cand:
    id: UUID
    doi: str
    title: str
    year: int | None
    norm: str = ""


def norm_doi(s) -> str:
    s = unquote(str(s or "")).strip().lower()
    s = re.sub(r"^(https?://(www\.)?(dx\.)?doi\.org/|doi:\s*)", "", s)
    return s.strip().rstrip(".,;)")


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
    return Cand(doc_id, norm_doi(m.get("doi")), t, _year(m.get("year")), utils.default_process(t))


def _candidates(session: Session, *, exclude: UUID | None = None, only: UUID | None = None) -> list[Cand]:
    stmt = (
        select(Document.id, Document.title, DocumentVersion.meta)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(
            Document.deleted_at.is_(None),
            DocumentVersion.is_latest.is_(True),
            DocumentVersion.stage == "searchable",
        )
        .order_by(Document.created_at, Document.id)  # deterministic: oldest document wins DOI clashes
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
        self.by_doi: dict[str, Cand] = {}
        for c in cands:
            if c.doi:
                self.by_doi.setdefault(c.doi, c)
        self.by_year: dict[int, list[Cand]] = {}
        for c in cands:
            if c.year is not None and _tokens_ok(c.norm):
                self.by_year.setdefault(c.year, []).append(c)

    def match(self, ref: dict) -> tuple[UUID, float] | None:
        doi = norm_doi(ref.get("doi"))
        if doi and doi in self.by_doi:
            return self.by_doi[doi].id, 1.0
        title, year = utils.default_process(str(ref.get("title") or "")), _year(ref.get("year"))
        if year is None or not _tokens_ok(title):
            return None
        scored = [(fuzz.token_sort_ratio(title, c.norm), c.id) for c in self.by_year.get(year, ())]
        top = max((r for r, _ in scored), default=0)
        best = [i for r, i in scored if r == top]
        if top < FUZZY_MIN or len(best) != 1:  # ambiguous tie: do not guess
            return None
        return best[0], top / 100


def resolve_references(session: Session, version: DocumentVersion) -> int:
    """Replace the version's citation rows (idempotent); returns the matched count."""
    index = _Index(_candidates(session, exclude=version.document_id))
    session.execute(delete(DocCitation).where(DocCitation.src_version_id == version.id))
    matched = 0
    for n, ref in enumerate(version.references or [], 1):
        ref = ref if isinstance(ref, dict) else {}
        hit = index.match(ref)
        matched += hit is not None
        session.add(
            DocCitation(
                src_version_id=version.id,
                ref_no=n,
                target_document_id=hit[0] if hit else None,
                confidence=hit[1] if hit else None,
                raw=ref,
            )
        )
    session.flush()
    return matched


def backfill_for(session: Session, document: Document) -> int:
    """Link dangling rows of OTHER documents' latest versions that cite `document`."""
    index = _Index(_candidates(session, only=document.id))
    if not index.by_doi and not index.by_year:
        return 0
    rows = session.scalars(
        select(DocCitation)
        .join(DocumentVersion, DocumentVersion.id == DocCitation.src_version_id)
        .where(
            DocCitation.target_document_id.is_(None),
            DocumentVersion.is_latest.is_(True),
            DocumentVersion.document_id != document.id,
        )
    ).all()
    n = 0
    for row in rows:
        hit = index.match(row.raw or {})
        if hit:
            row.target_document_id, row.confidence = hit
            n += 1
    session.flush()
    return n


def handle_citations(
    session: Session, payload: dict, *, session_factory: sessionmaker[Session] | None = None
) -> None:
    """Job `citations` {version_id}: runs inside run_once's SAVEPOINT; never commits (R6/R9)."""
    v = session.get(DocumentVersion, UUID(payload["version_id"]))
    if v is None or v.stage != "searchable":
        return
    d = session.get(Document, v.document_id)
    if d is None or d.deleted_at is not None:
        return
    resolve_references(session, v)
    backfill_for(session, d)


def cited_by_counts(session: Session, p: Principal, doc_ids: list[UUID]) -> dict[str, int]:
    """Distinct VISIBLE citing documents (latest searchable version) per target; one query."""
    if not doc_ids:
        return {}
    rows = session.execute(
        select(DocCitation.target_document_id, func.count(func.distinct(Document.id)))
        .join(DocumentVersion, DocumentVersion.id == DocCitation.src_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            DocCitation.target_document_id.in_(doc_ids),
            and_(DocumentVersion.is_latest.is_(True), DocumentVersion.stage == "searchable"),
            visible_documents(p),
        )
        .group_by(DocCitation.target_document_id)
    ).all()
    return {str(t): n for t, n in rows}
