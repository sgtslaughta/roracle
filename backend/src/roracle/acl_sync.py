"""`acl_sync` job: converge Elasticsearch ACL fields + title of one document to Postgres (idempotent)."""

from uuid import UUID

from elasticsearch import Elasticsearch
from sqlalchemy.orm import Session, sessionmaker

from roracle import search_index
from roracle.models import Document


def read_acl(session_factory: sessionmaker[Session], doc_id: UUID) -> tuple[dict, str] | None:
    """Fresh read-only short session: (acl fields, indexed title) or None if missing/deleted."""
    with session_factory() as s:
        d = s.get(Document, doc_id)
        if d is None or d.deleted_at is not None:
            return None
        return search_index.acl_fields(s, d), d.title or "none"  # "none" matches ingest


def handle_acl_sync(
    session: Session, payload: dict, *, es: Elasticsearch, session_factory: sessionmaker[Session]
) -> None:
    doc_id = UUID(payload["doc_id"])
    search_index.sync_acl(es, doc_id, lambda: read_acl(session_factory, doc_id))
