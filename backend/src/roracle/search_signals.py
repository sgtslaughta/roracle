"""Head-chunk signals (votes_net, cited_by_global, searchable): computed from Postgres, pushed to the one head
chunk of each version by `update_by_query` (a vote rewrites one ES doc per version, not every chunk)."""

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from elasticsearch import Elasticsearch
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from roracle.models import DocCitation, Document, DocumentVersion, Vote
from roracle.queue import enqueue
from roracle.search_index_acl import ACL_BATCH, MUTATE_ALIAS, run_update_by_query


def signals_for(session: Session, doc_ids: list[UUID]) -> dict[str, dict[str, Any]]:
    """`{version_id: signals}` for every version of the non-deleted documents. `cited_by_global` counts distinct
    non-deleted citing documents (latest searchable version) with ANY citation row to the document: an upper
    bound of every viewer's visible count."""
    if not doc_ids:
        return {}
    votes = dict(
        session.execute(
            select(Vote.document_id, func.sum(Vote.value))
            .where(Vote.document_id.in_(doc_ids))
            .group_by(Vote.document_id)
        ).all()
    )
    src = DocumentVersion
    cited = dict(
        session.execute(
            select(DocCitation.target_document_id, func.count(func.distinct(src.document_id)))
            .join(src, src.id == DocCitation.src_version_id)
            .join(Document, Document.id == src.document_id)
            .where(
                DocCitation.target_document_id.in_(doc_ids),
                src.is_latest.is_(True),
                src.stage == "searchable",
                Document.deleted_at.is_(None),
            )
            .group_by(DocCitation.target_document_id)
        ).all()
    )
    rows = session.execute(
        select(DocumentVersion.id, DocumentVersion.document_id, DocumentVersion.stage)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(DocumentVersion.document_id.in_(doc_ids), Document.deleted_at.is_(None))
    ).all()
    return {
        str(vid): {
            "votes_net": int(votes.get(did) or 0),
            "cited_by_global": int(cited.get(did) or 0),
            "searchable": stage == "searchable",
        }
        for vid, did, stage in rows
    }


# Compares before writing: unchanged head chunks are a `noop` (no reindex, no HNSW re-insert).
_SCRIPT = (
    "def f = params.sig[ctx._source.version_id]; if (f == null) { ctx.op = 'noop'; return; } "
    "boolean changed = false; "
    "for (def e : f.entrySet()) { "
    "if (!Objects.equals(ctx._source[e.getKey()], e.getValue())) { ctx._source[e.getKey()] = e.getValue(); "
    "changed = true; } } "
    "if (!changed) { ctx.op = 'noop'; }"
)


def sync_signals(es: Elasticsearch, session_factory: sessionmaker[Session], doc_ids: list[str]) -> int:
    """Converge the head-chunk signals of the documents, `ACL_BATCH` per update_by_query; PG is re-read on every
    try. Returns the number of chunks actually updated."""

    def build(ids: list[str]) -> dict[str, Any]:
        with session_factory() as s:
            sig = signals_for(s, [UUID(i) for i in ids])
        return {
            "index": MUTATE_ALIAS,
            "query": {"bool": {"filter": [{"terms": {"doc_id": ids}}, {"term": {"doc_head": True}}]}},
            "script": {"source": _SCRIPT, "lang": "painless", "params": {"sig": sig}},
        }

    updated = 0
    for i in range(0, len(doc_ids), ACL_BATCH):
        ids = doc_ids[i : i + ACL_BATCH]
        updated += (run_update_by_query(es, lambda ids=ids: build(ids)) or {}).get("updated", 0)
    return updated


def enqueue_signals(session: Session, doc_ids: Iterable[UUID]) -> None:
    ids = sorted(map(str, set(doc_ids)))
    if ids:
        enqueue(session, "search_signals", {"doc_ids": ids})


def handle_search_signals(
    session: Session, payload: dict, *, es: Elasticsearch, session_factory: sessionmaker[Session]
) -> None:
    sync_signals(es, session_factory, [str(UUID(str(i))) for i in payload.get("doc_ids", [])])
