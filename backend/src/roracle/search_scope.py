"""Doc-id scopes for library search: collections and `cites` (Postgres-resolved, ACL-safe)."""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.access import readable_documents, visible_documents
from roracle.auth import Principal
from roracle.citations import best_targets
from roracle.models import Collection, CollectionItem, Document, DocumentVersion
from roracle.search_limits import BadInput
from roracle.search_params import SearchParams

SCOPE_IDS_MAX = 10_000
COLLS_MAX = 50
_TOO_BIG = "collection or citation scope too large"


@dataclass
class DocScope:
    include: list[str] | None = None
    exclude: list[str] = field(default_factory=list)
    colls: dict[str, tuple[str, list[str]]] = field(default_factory=dict)  # id -> (name, doc ids)


def _uuids(vs: list[str]) -> list[UUID]:
    out = []
    for v in vs:
        try:
            out.append(UUID(v))
        except ValueError:
            pass
    return out


def _docs(session: Session, p: Principal, cids: list[UUID]) -> list[str]:
    owned = select(Collection.id).where(Collection.owner_id == p.user_id, Collection.id.in_(cids))
    rows = session.scalars(
        select(CollectionItem.document_id)
        .where(CollectionItem.collection_id.in_(owned))
        .distinct()
        .limit(SCOPE_IDS_MAX + 1)
    ).all()
    if len(rows) > SCOPE_IDS_MAX:
        raise BadInput(_TOO_BIG)
    return [str(d) for d in rows]


def _citers(session: Session, p: Principal, target: UUID) -> list[str] | None:
    """Visible latest-searchable docs whose best visible target for some reference is `target`."""
    if session.scalar(select(Document.id).where(Document.id == target, readable_documents(p))) is None:
        return None
    bt = best_targets(p, targets=[target])
    rows = session.scalars(
        select(DocumentVersion.document_id)
        .join(bt, bt.c.src_version_id == DocumentVersion.id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            bt.c.rn == 1,
            bt.c.target_document_id == target,
            DocumentVersion.is_latest.is_(True),
            DocumentVersion.stage == "searchable",
            visible_documents(p),
        )
        .distinct()
        .limit(SCOPE_IDS_MAX + 1)
    ).all()
    if len(rows) > SCOPE_IDS_MAX:
        raise BadInput(_TOO_BIG)
    return [str(d) for d in rows]


def _colls(session: Session, p: Principal) -> dict[str, tuple[str, list[str]]]:
    cs = session.execute(
        select(Collection.id, Collection.name)
        .where(Collection.owner_id == p.user_id)
        .order_by(Collection.name, Collection.id)  # ponytail: collections have no created_at column
        .limit(COLLS_MAX)
    ).all()
    docs: dict[UUID, list[str]] = {c.id: [] for c in cs}
    for cid, did in session.execute(
        select(CollectionItem.collection_id, CollectionItem.document_id).where(
            CollectionItem.collection_id.in_(list(docs))
        )
    ):
        docs[cid].append(str(did))
    return {str(c.id): (c.name, docs[c.id]) for c in cs}


def resolve_scope(session: Session, p: Principal, f: SearchParams) -> DocScope | None:
    """None = no result: a collection the caller does not own, or `cites` not visible / absent."""
    scope = DocScope(colls=_colls(session, p))
    if f.collection:
        cids = _uuids(f.collection)
        if len(cids) != len(set(f.collection)) or _owned(session, p, cids) != set(cids):
            return None  # unknown, malformed or foreign id: indistinguishable from absent
        scope.include = _docs(session, p, cids)
    if f.collection_not:
        scope.exclude = _docs(session, p, _uuids(f.collection_not))
    if f.cites is not None:
        ids = _citers(session, p, f.cites)
        if ids is None:
            return None
        scope.include = ids if scope.include is None else sorted(set(ids) & set(scope.include))
    return scope


def _owned(session: Session, p: Principal, cids: list[UUID]) -> set[UUID]:
    return set(
        session.scalars(
            select(Collection.id).where(Collection.owner_id == p.user_id, Collection.id.in_(cids))
        )
    )
