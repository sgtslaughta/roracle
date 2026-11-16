"""`acl_sync` job: converge Elasticsearch ACL fields + title of one document to Postgres (idempotent)."""

from uuid import UUID

from elasticsearch import Elasticsearch
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from roracle import search_index
from roracle.models import Document, Tag
from roracle.queue import enqueue, payload_uuid


def read_acl(session_factory: sessionmaker[Session], doc_id: UUID) -> tuple[dict, str] | None:
    """Fresh read-only short session: (acl fields + tags, indexed title) or None if missing/deleted."""
    with session_factory() as s:
        d = s.get(Document, doc_id)
        if d is None or d.deleted_at is not None:
            return None
        tags = list(s.scalars(select(Tag.tag).where(Tag.document_id == doc_id).order_by(Tag.tag)))
        return {**search_index.acl_fields(s, d), "tags": tags}, d.title or "none"  # "none" matches ingest


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
