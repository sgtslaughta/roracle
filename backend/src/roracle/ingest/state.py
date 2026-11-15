"""Ingest state writes.

RULE: during ingest ALL writes to document_versions (and documents) go through these helpers.
The handler's own session (inside run_once's SAVEPOINT, holding the job claim lock) is read-only
for those tables and must never commit. Each helper uses its OWN short session and commits; if the
handler session had already written the same row, the helper would wait on that lock forever, so
every helper sets a lock_timeout and fails loudly (retryable) instead.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.orm import Session, sessionmaker

from roracle.models import DocumentVersion

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
