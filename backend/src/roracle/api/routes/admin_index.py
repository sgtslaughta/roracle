"""Admin: embedding-model switch (background re-embed + alias cutover). See reembed.py."""

from typing import Annotated, Literal

from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.orm import Session

from roracle import reembed, runtime
from roracle.api.routes.admin_deps import Admin
from roracle.api.routes.library import get_es
from roracle.audit import audit
from roracle.db import get_session
from roracle.ingest import rebuild

router = APIRouter()
Db = Annotated[Session, Depends(get_session)]
Es = Annotated[Elasticsearch, Depends(get_es)]


class ReindexIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    model: Annotated[str, Field(min_length=1, max_length=200)]
    dims: Annotated[StrictInt, Field(ge=1, le=4096)]


@router.post("/admin/reindex", status_code=202)
def start_reindex(body: ReindexIn, p: Admin, session: Db, es: Es, request: Request) -> dict:
    try:
        job_id = reembed.start(session, es, request.app.state.settings, body.model, body.dims)
    except reembed.AlreadyRunning:
        raise HTTPException(409, "reindex already running") from None
    audit(session, p, "admin.reindex_start", "/admin/reindex", {"model": body.model, "dims": body.dims})
    return {"job_id": job_id}


@router.get("/admin/reindex")
def get_reindex(p: Admin, session: Db, es: Es, request: Request) -> dict:
    return reembed.status(session, es, request.app.state.settings)


@router.delete("/admin/reindex/old", status_code=204)
def delete_old(p: Admin, session: Db, es: Es, request: Request) -> None:
    if reembed.running_job(session) is not None:
        raise HTTPException(409, "reindex running")
    try:
        dropped = reembed.drop_old(session, es, request.app.state.settings)
    except reembed.AliasMismatch:
        raise HTTPException(409, "read alias does not point at the active index; resolve first") from None
    audit(session, p, "admin.reindex_drop_old", "/admin/reindex/old", {"indices": dropped})


class RebuildIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    what: Annotated[list[Literal["context", "raptor"]], Field(min_length=1, max_length=2)]
    into: Annotated[str, Field(min_length=1, max_length=120)] | None = None


@router.post("/admin/retrieval/rebuild", status_code=202)
def start_rebuild(body: RebuildIn, p: Admin, session: Db, es: Es, request: Request) -> dict:
    eff = runtime.effective(session, request.app.state.settings)
    try:
        jobs = rebuild.start(session, es, eff, set(body.what), body.into)
    except reembed.AlreadyRunning:
        raise HTTPException(409, "reindex running") from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    audit(session, p, "retrieval.rebuild", "/admin/retrieval/rebuild", {"what": body.what, "into": body.into})
    return {"jobs": jobs}
