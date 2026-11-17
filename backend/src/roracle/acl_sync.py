"""`acl_sync` job: converge Elasticsearch ACL fields + title of one document to Postgres (idempotent)."""

from collections import defaultdict
from collections.abc import Collection
from uuid import UUID

from elasticsearch import Elasticsearch, NotFoundError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from roracle import search_index
from roracle.models import Document, DocumentCoauthor, DocumentShare, Tag
from roracle.queue import enqueue, payload_uuid


def read_acl(session_factory: sessionmaker[Session], doc_id: UUID) -> tuple[dict, str] | None:
    """Fresh read-only short session: (acl fields + tags, indexed title) or None if missing/deleted."""
    with session_factory() as s:
        d = s.get(Document, doc_id)
        if d is None or d.deleted_at is not None:
            return None
        tags = sorted(
            s.scalars(select(Tag.tag).where(Tag.document_id == doc_id))
        )  # Python order, as read_acl_many
        return {**search_index.acl_fields(s, d), "tags": tags}, d.title or "none"  # "none" matches ingest


def read_acl_many(session: Session, doc_ids: Collection[UUID]) -> dict[str, tuple[dict, str]]:
    """`read_acl` for many live documents in 4 queries: `str(doc_id) -> (acl fields + tags, title)`.
    Identical to `read_acl` per document (tests assert it)."""
    docs = {d.id: d for d in session.scalars(select(Document).where(Document.id.in_(doc_ids)))}
    docs = {i: d for i, d in docs.items() if d.deleted_at is None}
    shares: dict[UUID, list] = defaultdict(list)
    for doc_id, kind, principal in session.execute(
        select(DocumentShare.document_id, DocumentShare.principal_kind, DocumentShare.principal).where(
            DocumentShare.document_id.in_(docs)
        )
    ):
        shares[doc_id].append((kind, principal))
    coauthors: dict[UUID, list] = defaultdict(list)
    for doc_id, user_id in session.execute(
        select(DocumentCoauthor.document_id, DocumentCoauthor.user_id).where(
            DocumentCoauthor.document_id.in_(docs)
        )
    ):
        coauthors[doc_id].append(str(user_id))
    tags: dict[UUID, list] = defaultdict(list)
    for doc_id, tag in session.execute(select(Tag.document_id, Tag.tag).where(Tag.document_id.in_(docs))):
        tags[doc_id].append(tag)
    return {
        str(i): (
            {
                "owner_id": str(d.owner_id),
                "coauthor_users": sorted(coauthors[i]),
                "visibility": d.visibility,
                "org_unit": d.org_unit,
                "share_users": sorted(p for k, p in shares[i] if k == "user"),
                "share_groups": sorted(p for k, p in shares[i] if k == "group"),
                "label_id": str(d.label_id),
                "tags": sorted(tags[i]),
            },
            d.title or "none",
        )
        for i, d in docs.items()
    }


DRIFT_QUERY_DOCS = 200  # docs per search: keeps the bool clause count (3 per doc) far below max_clause_count


def drifted(es: Elasticsearch, items: dict[str, tuple[dict, str]]) -> set[str]:
    """Doc ids with at least one chunk/unit whose `acl_hash` differs from Postgres (or has none)."""
    found: set[str] = set()
    keys = list(items)
    for i in range(0, len(keys), DRIFT_QUERY_DOCS):
        part = keys[i : i + DRIFT_QUERY_DOCS]
        should = [
            {
                "bool": {
                    "filter": [{"term": {"doc_id": d}}],
                    "must_not": [{"term": {"acl_hash": search_index.acl_hash(*items[d])}}],
                }
            }
            for d in part
        ]
        try:
            r = es.search(
                index=search_index.MUTATE_ALIAS,
                query={"bool": {"should": should, "minimum_should_match": 1}},
                collapse={"field": "doc_id"},
                source=False,
                size=len(part),
                docvalue_fields=["doc_id"],
            )
        except NotFoundError:
            return found
        found |= {h["fields"]["doc_id"][0] for h in r["hits"]["hits"]}
    return found


def handle_acl_sync(
    session: Session, payload: dict, *, es: Elasticsearch, session_factory: sessionmaker[Session]
) -> None:
    doc_id = payload_uuid(payload, "doc_id")
    search_index.sync_acl(es, doc_id, lambda: read_acl(session_factory, doc_id))


def durable_job(sf: sessionmaker[Session], doc_id: UUID, delay_s: float = 0) -> None:
    """Commit an `acl_sync` job in a fresh session so the index converges even if the caller dies."""
    with sf() as s:
        enqueue(s, "acl_sync", {"doc_id": str(doc_id)}, run_after_s=delay_s)
        s.commit()
