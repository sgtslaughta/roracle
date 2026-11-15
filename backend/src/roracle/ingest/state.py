"""Ingest state writes. Each helper uses its OWN short session and commits.

Handlers run inside a SAVEPOINT holding the job claim lock and must never commit, so durable
progress (stage, crash counter) goes through a separate session.
"""

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from roracle.models import DocumentVersion


def set_stage(session_factory: sessionmaker[Session], version_id: UUID, stage: str) -> None:
    with session_factory() as s:
        s.execute(update(DocumentVersion).where(DocumentVersion.id == version_id).values(stage=stage))
        s.commit()


def bump_attempts(session_factory: sessionmaker[Session], version_id: UUID) -> int:
    with session_factory() as s:
        n = s.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == version_id)
            .values(ingest_attempts=DocumentVersion.ingest_attempts + 1)
            .returning(DocumentVersion.ingest_attempts)
        ).scalar_one()
        s.commit()
        return n
