"""Shared intake: store the blob, create rows, enqueue ingest. Adds to the caller's transaction (no commit)."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from roracle.models import Blob, Document, DocumentVersion
from roracle.queue import enqueue
from roracle.storage import BlobStore, StagedBlob, StagedFile


def _put(session: Session, store: BlobStore, staged: StagedFile | StagedBlob, content_type: str) -> None:
    # ponytail: object written before DB commit can orphan on DB failure; a P2 GC job deletes blob objects without a blobs row
    if isinstance(staged, StagedFile):  # streamed uploads were promoted by the route before intake
        store.put(staged, content_type)
    session.execute(
        insert(Blob)
        .values(sha256=staged.sha256, size=staged.size)
        .on_conflict_do_update(index_elements=[Blob.sha256], set_={"refcount": Blob.refcount + 1})
    )


def _version(
    session: Session,
    staged: StagedFile | StagedBlob,
    doc_id: UUID,
    number: int,
    filename: str,
    content_type: str,
) -> DocumentVersion:
    version = DocumentVersion(
        document_id=doc_id,
        version=number,
        blob_sha256=staged.sha256,
        filename=filename,
        content_type=content_type,
        size=staged.size,
    )
    session.add(version)
    session.flush()
    enqueue(session, "ingest", {"version_id": str(version.id)})
    return version


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
) -> tuple[Document, DocumentVersion]:
    _put(session, store, staged, content_type)
    doc = Document(
        owner_id=owner_id, title=title, org_unit=org_unit, visibility=visibility, connector_id=connector_id
    )
    session.add(doc)
    session.flush()
    return doc, _version(session, staged, doc.id, 1, filename, content_type)


def add_version(
    session: Session,
    store: BlobStore,
    staged: StagedFile | StagedBlob,
    doc: Document,
    *,
    filename: str,
    content_type: str,
) -> DocumentVersion:
    """Next version of `doc`; the ingest pipeline demotes older versions' `is_latest`."""
    session.get(Document, doc.id, with_for_update=True)  # serialize concurrent version numbering
    top = session.scalar(
        select(func.max(DocumentVersion.version)).where(DocumentVersion.document_id == doc.id)
    )
    _put(session, store, staged, content_type)
    return _version(session, staged, doc.id, (top or 0) + 1, filename, content_type)
