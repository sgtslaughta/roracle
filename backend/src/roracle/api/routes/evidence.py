"""Evidence REST (P8 task 5). Writes are JWT-only (R3); files never served inline (R7)."""

from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from roracle.api.routes.documents import get_blob_store
from roracle.audit import audit
from roracle.auth import Principal, current_principal, get_app_settings, require_human
from roracle.config import Settings
from roracle.db import get_session
from roracle.models import Blob, Evidence, EvidenceReproduction
from roracle.notes import evidence as ev
from roracle.notes.access import get_readable
from roracle.notes.service import NOT_FOUND
from roracle.queue import enqueue
from roracle.storage import BlobStore, EmptyFile, TooLarge, stage

router = APIRouter()
Session_ = Annotated[Session, Depends(get_session)]
Who = Annotated[Principal, Depends(current_principal)]
Human = Annotated[Principal, Depends(require_human)]
Store = Annotated[BlobStore, Depends(get_blob_store)]
Cfg = Annotated[Settings, Depends(get_app_settings)]
CHUNK = 1 << 20


def _load(session: Session, p: Principal, evidence_id: UUID) -> Evidence:
    e = session.get(Evidence, evidence_id)
    if e is None:
        raise NOT_FOUND
    get_readable(session, p, e.annotation_id)
    return e


def _meta(raw: str) -> ev.EvidenceMeta:
    if len(raw.encode()) > ev.META_MAX:
        raise HTTPException(422, "meta too large")
    try:
        return ev.EvidenceMeta.model_validate_json(raw)
    except ValidationError as ex:
        raise HTTPException(
            422, ex.errors(include_url=False, include_context=False, include_input=False)
        ) from None


@router.post("/annotations/{note_id}/evidence", status_code=201)
def create(
    note_id: UUID,
    session: Session_,
    p: Human,
    store: Store,
    settings: Cfg,
    meta: Annotated[str, Form()],
    file: Annotated[UploadFile | None, File()] = None,
) -> dict:
    note = get_readable(session, p, note_id)
    if note.kind != "finding":
        raise HTTPException(422, "evidence requires a finding note")
    m = _meta(meta)
    if file is not None and m.uri:
        raise HTTPException(422, "provide either a file or a uri, not both")
    e = Evidence(annotation_id=note.id, creator_id=p.user_id, stance=m.stance, crate=ev.crate(m))
    staged = None
    try:
        if file is not None:
            try:
                staged = stage(file.file, settings.evidence_max_bytes)
            except EmptyFile:
                raise HTTPException(400, "empty file") from None
            except TooLarge:
                raise HTTPException(413, "file exceeds the evidence size limit") from None
            store.put(staged, "application/octet-stream")
            session.execute(
                insert(Blob)
                .values(sha256=staged.sha256, size=staged.size)
                .on_conflict_do_update(index_elements=[Blob.sha256], set_={"refcount": Blob.refcount + 1})
            )
            e.blob_sha256, e.size, e.scan_status = staged.sha256, staged.size, "pending"
            e.filename = ev.safe_filename(file.filename or "")
        else:
            e.uri, e.uri_sha256 = m.uri, m.uri_sha256
        session.add(e)
        session.flush()
        if staged:
            enqueue(session, "evidence_scan", {"evidence_id": str(e.id)})
        out = ev.to_json(session, p, [e])[0]
        session.commit()
        return out
    finally:
        if staged:
            Path(staged.path).unlink(missing_ok=True)


@router.get("/annotations/{note_id}/evidence")
def list_evidence(note_id: UUID, session: Session_, p: Who) -> dict:
    get_readable(session, p, note_id)
    rows = list(
        session.scalars(
            select(Evidence)
            .where(Evidence.annotation_id == note_id)
            .order_by(Evidence.created_at, Evidence.id)
        )
    )
    return {"items": ev.to_json(session, p, rows)}


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: UUID, session: Session_, p: Who) -> dict:
    return ev.to_json(session, p, [_load(session, p, evidence_id)])[0]


@router.get("/evidence/{evidence_id}/file")
def download(evidence_id: UUID, session: Session_, p: Who, store: Store) -> StreamingResponse:
    e = _load(session, p, evidence_id)
    if e.blob_sha256 is None or e.scan_status not in ("clean", "unscanned"):
        raise HTTPException(409, "not available")
    body = store.open(e.blob_sha256)

    def chunks():
        try:
            while data := body.read(CHUNK):
                yield data
        finally:
            body.close()

    return StreamingResponse(
        chunks(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": ev.content_disposition(ev.safe_filename(e.filename)),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            **({"Content-Length": str(e.size)} if e.size is not None else {}),
        },
    )


class ReproIn(BaseModel):
    outcome: Literal["reproduced", "failed"] | None
    note: str = Field("", max_length=2000)


@router.put("/evidence/{evidence_id}/reproduction")
def reproduce(evidence_id: UUID, body: ReproIn, session: Session_, p: Human) -> dict:
    e = _load(session, p, evidence_id)
    if body.outcome is None:
        session.execute(
            sa_delete(EvidenceReproduction).where(
                EvidenceReproduction.evidence_id == e.id, EvidenceReproduction.user_id == p.user_id
            )
        )
    else:
        ins = insert(EvidenceReproduction).values(
            evidence_id=e.id, user_id=p.user_id, outcome=body.outcome, note=body.note
        )
        session.execute(
            ins.on_conflict_do_update(
                index_elements=["evidence_id", "user_id"],
                set_={"outcome": ins.excluded.outcome, "note": ins.excluded.note, "created_at": func.now()},
            )
        )
    session.flush()
    out = ev.to_json(session, p, [e])[0]
    session.commit()
    return out


@router.delete("/evidence/{evidence_id}", status_code=204)
def delete(evidence_id: UUID, session: Session_, p: Human) -> Response:
    e = _load(session, p, evidence_id)
    if e.creator_id != p.user_id and not p.is_admin:
        raise HTTPException(403, "only the creator or an admin can delete evidence")
    if e.creator_id != p.user_id:
        audit(session, p, "evidence.delete", str(e.id), {"annotation_id": str(e.annotation_id)})
    if e.blob_sha256:
        session.execute(
            Blob.__table__.update()
            .where(Blob.sha256 == e.blob_sha256)
            .values(refcount=func.greatest(Blob.refcount - 1, 0))
        )
    session.delete(e)
    session.commit()
    return Response(status_code=204)
