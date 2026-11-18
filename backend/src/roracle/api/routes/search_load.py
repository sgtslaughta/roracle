"""Row loading for /search: Postgres-side visibility, facet filters, keyset cursor, narrow version columns; hit shape."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, case, exists, false, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import Session, load_only

from roracle.access import visible_documents
from roracle.auth import Principal
from roracle.cursor import make_cursor
from roracle.identifiers import public_ids
from roracle.labels.policy import Policy
from roracle.models import Collection, CollectionItem, Document, DocumentVersion, Tag, Vote
from roracle.retrieval import Passage
from roracle.search_index import file_type as ext_of


@dataclass
class Row:
    doc: Document
    ver: DocumentVersion
    tags: list[str] = field(default_factory=list)
    auto_tags: list[str] = field(default_factory=list)  # subset of `tags` suggested by the LLM
    votes: tuple[int, int, int] = (0, 0, 0)  # up, down, mine
    colls: list[tuple[str, str]] = field(default_factory=list)  # caller's (id, name)
    score: float = 0.0
    passages: list[Passage] = field(default_factory=list)

    @property
    def meta(self) -> dict:
        return self.ver.meta or {}

    @property
    def title(self) -> str:
        return self.doc.title or self.meta.get("title") or self.ver.filename

    def facet_values(self) -> dict[str, list[str]]:
        year = self.meta.get("year")
        return {
            "tags": self.tags,
            "year": [] if year is None else [str(year)],
            "file_type": [ext_of(self.ver.filename)],
            "org_unit": [self.doc.org_unit],
            "collection": [c for c, _ in self.colls],
            "author": list(self.meta.get("authors") or []),
            "label": [str(self.doc.label_id)],
        }


def _votes(session: Session, ids: list[UUID], uid: UUID) -> dict[str, tuple[int, int, int]]:
    mine = func.coalesce(func.max(case((Vote.user_id == uid, Vote.value))), 0)
    rows = session.execute(
        select(
            Vote.document_id,
            func.count().filter(Vote.value == 1),
            func.count().filter(Vote.value == -1),
            mine,
        )
        .where(Vote.document_id.in_(ids))
        .group_by(Vote.document_id)
    ).all()
    return {str(d): (u, n, m) for d, u, n, m in rows}


def _is_uuid(x: str) -> bool:
    try:
        UUID(x)
    except ValueError:
        return False
    return True


def _file_type_clause(want: set[str]):
    """SQL twin of search_index.file_type: lowercase extension of 1-8 [a-z0-9] chars, else "other"."""
    name = func.lower(DocumentVersion.filename)
    ext = r"\.[a-z0-9]{1,8}$"
    exts = sorted(w for w in want if w != "other" and re.fullmatch(r"[a-z0-9]{1,8}", w))
    parts = []
    if exts:
        parts.append(name.op("~")(r"\.(" + "|".join(exts) + ")$"))
    if "other" in want:
        parts.append(~name.op("~")(ext))
    return or_(*parts) if parts else false()


# The only DocumentVersion columns a `Row` reads. Everything else (minhash, summary_embedding, references,
# summary: ~4 KB per row) is never loaded; `raiseload` makes any other access an error instead of a lazy query.
ROW_COLUMNS = (
    DocumentVersion.id,
    DocumentVersion.document_id,
    DocumentVersion.version,
    DocumentVersion.filename,
    DocumentVersion.created_at,
    DocumentVersion.meta,
    DocumentVersion.page_count,
)


def cursor_of(row: "Row") -> str:
    return make_cursor(row.ver.created_at, row.doc.id)


def load_stmt(
    p: Principal,
    ids: list[UUID] | None,
    wanted: dict[str, set[str]] | None = None,
    limit: int | None = None,
    columns=ROW_COLUMNS,
    after: tuple[datetime, UUID] | None = None,
):
    """Visible documents with a searchable latest version, newest first.

    `wanted` pushes every facet filter down to SQL, so `limit` is always safe. `after` is the keyset cursor of
    the (created_at DESC, document id ASC) order: only rows strictly after it."""
    stmt = (
        select(Document, DocumentVersion)
        .options(load_only(*columns, raiseload=True))
        .join(
            DocumentVersion,
            and_(
                DocumentVersion.document_id == Document.id,
                # literal, not `IS true` / a bind: both must imply ix_versions_latest_searchable's predicate
                DocumentVersion.is_latest,
                DocumentVersion.stage == literal_column("'searchable'"),
            ),
        )
        .where(visible_documents(p))
        .order_by(DocumentVersion.created_at.desc(), Document.id)
    )
    if ids is not None:
        stmt = stmt.where(Document.id.in_(ids))
    if after is not None:
        at, last = after
        stmt = stmt.where(
            or_(
                DocumentVersion.created_at < at,
                and_(DocumentVersion.created_at == at, Document.id > last),
            )
        )
    for name, want in (wanted or {}).items():
        if not want:
            continue
        if name == "year":
            stmt = stmt.where(DocumentVersion.meta["year"].astext.in_(want))
        elif name == "org_unit":
            stmt = stmt.where(Document.org_unit.in_(want))
        elif name == "tags":
            stmt = stmt.where(exists().where(Tag.document_id == Document.id, Tag.tag.in_(want)))
        elif name == "author":
            stmt = stmt.where(DocumentVersion.meta["authors"].has_any(array(sorted(want))))
        elif name == "label":
            stmt = stmt.where(Document.label_id.in_([UUID(x) for x in want if _is_uuid(x)]))
        elif name == "file_type":
            stmt = stmt.where(_file_type_clause(want))
        elif name == "collection":
            stmt = stmt.where(
                Document.id.in_(
                    select(CollectionItem.document_id)
                    .join(Collection, Collection.id == CollectionItem.collection_id)
                    .where(Collection.owner_id == p.user_id, Collection.id.in_([UUID(c) for c in want]))
                )
            )
    if limit is not None:
        stmt = stmt.limit(limit)
    return stmt


def _load(
    session: Session,
    p: Principal,
    ids: list[UUID] | None,
    wanted: dict[str, set[str]] | None = None,
    limit: int | None = None,
    columns=ROW_COLUMNS,
    after: tuple[datetime, UUID] | None = None,
    votes: dict[str, tuple[int, int, int]] | None = None,
) -> list[Row]:
    """`load_stmt` rows enriched from Postgres (tags, caller's collections, votes; pass `votes` if the caller
    already has them for these ids)."""
    stmt = load_stmt(p, ids, wanted, limit, columns, after)
    rows = {d.id: Row(d, v) for d, v in session.execute(stmt).all()}
    if rows:
        keys = list(rows)
        gated = Policy.load(session).ids("exclude_llm") if p.is_agent else frozenset()  # agents: no LLM tags
        for doc_id, tag, src in session.execute(
            select(Tag.document_id, Tag.tag, Tag.source).where(Tag.document_id.in_(keys))
        ):
            if src != "user" and str(rows[doc_id].doc.label_id) in gated:
                continue
            rows[doc_id].tags.append(tag)
            if src != "user":
                rows[doc_id].auto_tags.append(tag)
        for r in rows.values():
            r.tags.sort()
            r.auto_tags.sort()
        for doc_id, cid, name in session.execute(
            select(CollectionItem.document_id, Collection.id, Collection.name)
            .join(Collection, Collection.id == CollectionItem.collection_id)
            .where(Collection.owner_id == p.user_id, CollectionItem.document_id.in_(keys))
        ):
            rows[doc_id].colls.append((str(cid), name))
        for k, v in (votes if votes is not None else _votes(session, keys, p.user_id)).items():
            if UUID(k) in rows:
                rows[UUID(k)].votes = v
    return list(rows.values())


def _own_collections(session: Session, p: Principal, values: list[str]) -> tuple[list[str], list[str]]:
    """R4/R10: the caller's collections among `values` -> (collection ids, member doc ids), both str."""
    cids = [UUID(v) for v in values if _is_uuid(v)]
    owned = list(
        session.scalars(
            select(Collection.id).where(Collection.owner_id == p.user_id, Collection.id.in_(cids))
        )
    )
    docs = session.scalars(
        select(CollectionItem.document_id).where(CollectionItem.collection_id.in_(owned)).distinct()
    )
    return [str(c) for c in owned], [str(d) for d in docs]


def _passage(x: Passage) -> dict:
    bbox = {k: v for k, v in x.bboxes[0].items() if k != "page"} if x.bboxes else None
    return {
        "page": x.pages[0] if x.pages else 1,
        "chunk_id": x.chunk_id,
        "text": x.text,
        "highlight": x.highlight,
        "bbox": bbox,
    }


def _hit(r: Row) -> dict:
    return {
        "doc_id": str(r.doc.id),
        "title": r.title,
        "authors": list(r.meta.get("authors") or []),
        "year": r.meta.get("year"),
        "score": r.score,
        "passages": [_passage(x) for x in r.passages],
        "tags": r.tags,
        "auto_tags": r.auto_tags,
        "votes": {"up": r.votes[0], "down": r.votes[1], "mine": r.votes[2]},
        "file_type": ext_of(r.ver.filename),
        "version": r.ver.version,
        "venue": r.meta.get("venue"),
        "org_unit": r.doc.org_unit,
        "owner_id": str(r.doc.owner_id),
        "label": str(r.doc.label_id),  # id here; the route swaps in the label JSON
        "flag": None,  # outdated-flag lands later
        "cited_by_count": 0,  # filled by the route in one batched query
        "page_count": r.ver.page_count,
        "added_at": r.doc.created_at.isoformat() if r.doc.created_at else None,
        "ids": public_ids(r.meta),
    }
