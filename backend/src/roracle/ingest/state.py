"""Ingest state writes.

RULE: during ingest ALL writes to document_versions (and documents) go through these helpers.
The handler's own session (inside run_once's SAVEPOINT, holding the job claim lock) is read-only
for those tables and must never commit. Each helper uses its OWN short session and commits; if the
handler session had already written the same row, the helper would wait on that lock forever, so
every helper sets a lock_timeout and fails loudly (retryable) instead.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import exists, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from roracle.models import Document, DocumentVersion, Vote
from roracle.queue import enqueue

LOCK_TIMEOUT_S = 10.0


def _lock_timeout(s: Session, seconds: float) -> None:
    s.execute(text("SELECT set_config('lock_timeout', :v, true)"), {"v": f"{int(seconds * 1000)}ms"})


def update_version(
    session_factory: sessionmaker[Session],
    version_id: UUID,
    *,
    lock_timeout_s: float = LOCK_TIMEOUT_S,
    **fields: Any,
) -> None:
    with session_factory() as s:
        _lock_timeout(s, lock_timeout_s)
        res = s.execute(update(DocumentVersion).where(DocumentVersion.id == version_id).values(**fields))
        if res.rowcount == 0:
            raise LookupError(f"document version {version_id} not found")
        s.commit()


def update_document(
    session_factory: sessionmaker[Session],
    doc_id: UUID,
    *,
    lock_timeout_s: float = LOCK_TIMEOUT_S,
    **fields: Any,
) -> None:
    with session_factory() as s:
        _lock_timeout(s, lock_timeout_s)
        res = s.execute(update(Document).where(Document.id == doc_id).values(**fields))
        if res.rowcount == 0:
            raise LookupError(f"document {doc_id} not found")
        s.commit()


def set_stage(
    session_factory: sessionmaker[Session],
    version_id: UUID,
    stage: str,
    *,
    lock_timeout_s: float = LOCK_TIMEOUT_S,
) -> None:
    update_version(session_factory, version_id, lock_timeout_s=lock_timeout_s, stage=stage)


def bump_attempts(
    session_factory: sessionmaker[Session], version_id: UUID, *, lock_timeout_s: float = LOCK_TIMEOUT_S
) -> int:
    with session_factory() as s:
        _lock_timeout(s, lock_timeout_s)
        n = s.scalar(
            update(DocumentVersion)
            .where(DocumentVersion.id == version_id)
            .values(ingest_attempts=DocumentVersion.ingest_attempts + 1)
            .returning(DocumentVersion.ingest_attempts)
        )
        if n is None:
            raise LookupError(f"document version {version_id} not found")
        s.commit()
        return n


def demote_older_versions(
    session_factory: sessionmaker[Session],
    doc_id: UUID,
    version_id: UUID,
    *,
    lock_timeout_s: float = LOCK_TIMEOUT_S,
) -> None:
    with session_factory() as s:
        _lock_timeout(s, lock_timeout_s)
        number = s.scalar(select(DocumentVersion.version).where(DocumentVersion.id == version_id))
        if number is None:
            raise LookupError(f"document version {version_id} not found")
        s.execute(
            update(DocumentVersion)
            .where(DocumentVersion.document_id == doc_id, DocumentVersion.version < number)
            .values(is_latest=False)
        )
        # A newer version may have moved in meanwhile: it keeps the flag, this one then is not latest.
        newer = exists().where(DocumentVersion.document_id == doc_id, DocumentVersion.version > number)
        s.execute(update(DocumentVersion).where(DocumentVersion.id == version_id).values(is_latest=~newer))
        s.commit()


def clear_outdated_reasons(
    session_factory: sessionmaker[Session],
    doc_id: UUID,
    version_id: UUID,
    *,
    lock_timeout_s: float = LOCK_TIMEOUT_S,
) -> None:
    """A new latest version makes `outdated` downvotes stale: keep the votes, drop the reason (R8)."""
    with session_factory() as s:
        _lock_timeout(s, lock_timeout_s)
        made = select(DocumentVersion.created_at).where(DocumentVersion.id == version_id).scalar_subquery()
        s.execute(  # only votes cast before this version: idempotent on re-ingest
            update(Vote)
            .where(Vote.document_id == doc_id, Vote.reason == "outdated", Vote.created_at < made)
            .values(reason=None)
        )
        s.commit()


def enqueue_job(session_factory: sessionmaker[Session], kind: str, payload: dict) -> None:
    """Enqueue a follow-up job from a short session of its own (never the handler's)."""
    with session_factory() as s:
        enqueue(s, kind, payload)
        s.commit()
