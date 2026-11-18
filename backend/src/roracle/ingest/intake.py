"""Shared intake: store the blob, create rows, enqueue ingest. Adds to the caller's transaction (no commit)."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from roracle.models import Blob, Document, DocumentVersion, Job
from roracle.queue import enqueue
from roracle.storage import BlobStore, StagedBlob, StagedFile


def ref_blob(session: Session, sha256: str, size: int) -> None:
    """Upsert the `blobs` row (takes its row lock). Do this BEFORE writing the S3 object: blob GC locks the
    row FOR UPDATE SKIP LOCKED, so it either skips us or we wait and re-copy after it commits."""
    session.execute(
        insert(Blob)
        .values(sha256=sha256, size=size)
        .on_conflict_do_update(index_elements=[Blob.sha256], set_={"refcount": Blob.refcount + 1})
    )


def _put(session: Session, store: BlobStore, staged: StagedFile | StagedBlob, content_type: str) -> None:
    # ponytail: object written before DB commit can orphan on DB failure; blob GC removes it later
    ref_blob(session, staged.sha256, staged.size)
    if isinstance(staged, StagedBlob):  # streamed upload: server-side copy tmp -> content key (always copies)
        store.promote(staged, content_type)
    else:
        store.put(staged, content_type)


def _version(
    session: Session,
    staged: StagedFile | StagedBlob,
    doc_id: UUID,
    number: int,
    filename: str,
    content_type: str,
    agent: dict | None = None,
) -> DocumentVersion:
    version = DocumentVersion(
        document_id=doc_id,
        version=number,
        blob_sha256=staged.sha256,
        filename=filename,
        content_type=content_type,
        size=staged.size,
        is_latest=number == 1,  # ingest flips the flag once a newer version is searchable
    )
    session.add(version)
    session.flush()
    enqueue(session, "ingest", ingest_payload(version.id, agent))
    return version


def ingest_payload(version_id: UUID, agent: dict | None) -> dict:
    """`agent` (auth.agent_marker) makes ingest-time checks (near-dup) run with the agent uploader's view."""
    return {"version_id": str(version_id), **({"agent": agent} if agent else {})}


def prior_agent(session: Session, version_id: UUID) -> dict | None:
    """The agent marker of the version's latest ingest job, to carry over when ingest is restarted."""
    payload = session.scalar(
        select(Job.payload)
        .where(Job.kind == "ingest", Job.payload["version_id"].astext == str(version_id))
        .order_by(Job.id.desc())
        .limit(1)
    )
    return (payload or {}).get("agent")


def add_document(
    session: Session,
    store: BlobStore,
    staged: StagedFile | StagedBlob,
    *,
    owner_id: UUID,
    org_unit: str,
    visibility: str,
    filename: str,
    content_type: str,
    title: str | None = None,
    connector_id: UUID | None = None,
    label_id: UUID | None = None,
    agent: dict | None = None,
) -> tuple[Document, DocumentVersion]:
    _put(session, store, staged, content_type)
    extra = {"label_id": label_id} if label_id else {}  # else the column default (global default label)
    doc = Document(
        owner_id=owner_id,
        title=title,
        org_unit=org_unit,
        visibility=visibility,
        connector_id=connector_id,
        **extra,
    )
    session.add(doc)
    session.flush()
    return doc, _version(session, staged, doc.id, 1, filename, content_type, agent)


def add_version(
    session: Session,
    store: BlobStore,
    staged: StagedFile | StagedBlob,
    doc: Document,
    *,
    filename: str,
    content_type: str,
    agent: dict | None = None,
) -> DocumentVersion:
    """Next version of `doc`; the ingest pipeline demotes older versions' `is_latest`."""
    session.get(Document, doc.id, with_for_update=True)  # serialize concurrent version numbering
    top = session.scalar(
        select(func.max(DocumentVersion.version)).where(DocumentVersion.document_id == doc.id)
    )
    _put(session, store, staged, content_type)
    return _version(session, staged, doc.id, (top or 0) + 1, filename, content_type, agent)
