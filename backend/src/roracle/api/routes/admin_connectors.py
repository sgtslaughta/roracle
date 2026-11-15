"""Admin: connector CRUD, manual runs and run history. Secrets are write-only ("***" on read)."""

from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from roracle.api.routes.admin_deps import Admin, keyset_page, keyset_params
from roracle.audit import audit
from roracle.connectors import fs
from roracle.connectors.netguard import Blocked, check_url
from roracle.connectors.run import active_run_job
from roracle.db import get_session
from roracle.models import Connector, ConnectorRun, Document
from roracle.queue import enqueue

router = APIRouter()
Db = Annotated[Session, Depends(get_session)]
MASK = "***"
Name = Annotated[str, Field(min_length=1, max_length=200)]
Schedule = Annotated[int, Field(ge=5, le=10080)] | None


class S3Config(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    endpoint: Annotated[str, Field(pattern=r"^https?://\S+$", max_length=500)]
    bucket: Annotated[str, Field(min_length=1, max_length=255)]
    prefix: Annotated[str, Field(max_length=1024)] = ""
    region: Annotated[str, Field(min_length=1, max_length=64)] = "us-east-1"
    access_key: Annotated[str, Field(max_length=256)] | None = None
    secret_key: Annotated[str, Field(max_length=256)] | None = None


class FsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    path: Annotated[str, Field(min_length=1, max_length=4096)]
    include: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=200)]], Field(min_length=1, max_length=20)
    ] = ["*"]


def _http_url(v: str) -> str:
    try:
        return check_url(v)  # scheme, host, no credentials; IDNA host and percent-encoding normalised
    except Blocked as e:
        raise ValueError(str(e)) from None


class HttpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    start_urls: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(_http_url)]],
        Field(min_length=1, max_length=20),
    ]
    max_depth: Annotated[int, Field(ge=0, le=3)] = 1
    max_pages: Annotated[int, Field(ge=1, le=1000)] = 100
    respect_robots: bool = True
    ingest_html: bool = False


CONFIGS: dict[str, type[BaseModel]] = {"s3": S3Config, "fs": FsConfig, "http": HttpConfig}


class Common(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: Name
    visibility: Literal["private", "org_unit", "everyone"]
    org_unit: Annotated[str, Field(max_length=500)] = ""
    schedule_minutes: Schedule = None
    enabled: bool = True


class S3In(Common):
    kind: Literal["s3"]
    config: S3Config


class FsIn(Common):
    kind: Literal["fs"]
    config: FsConfig


class HttpIn(Common):
    kind: Literal["http"]
    config: HttpConfig


ConnectorIn = Annotated[S3In | FsIn | HttpIn, Field(discriminator="kind")]


class ConnectorPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: Name | None = None
    config: dict[str, Any] | None = None
    visibility: Literal["private", "org_unit", "everyone"] | None = None
    org_unit: Annotated[str, Field(max_length=500)] | None = None
    schedule_minutes: Schedule = None
    enabled: bool | None = None


def redact(config: dict) -> dict:
    return {**config, "secret_key": MASK} if config.get("secret_key") else dict(config)


def _check_scope(visibility: str, org_unit: str) -> str:
    if visibility == "org_unit":
        if not org_unit.startswith("/"):
            raise HTTPException(422, "org_unit must start with '/' when visibility is org_unit")
        return org_unit
    return ""


def _validate_config(kind: str, config: dict, request: Request) -> dict:
    try:
        out = CONFIGS[kind].model_validate(config).model_dump()
    except ValidationError as e:
        raise HTTPException(
            422, e.errors(include_url=False, include_context=False, include_input=False)
        ) from None
    if kind == "s3":
        u = urlsplit(out["endpoint"])
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
            raise HTTPException(422, "endpoint must be an http(s) URL without credentials")
    if kind == "fs":
        try:
            fs.validate_path(out["path"], request.app.state.settings.connector_fs_roots)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
    return out


def _out(c: Connector) -> dict:
    out = {
        "id": str(c.id),
        "name": c.name,
        "kind": c.kind,
        "config": redact(c.config),
        "owner_id": str(c.owner_id),
        "visibility": c.visibility,
        "org_unit": c.org_unit,
        "schedule_minutes": c.schedule_minutes,
        "enabled": c.enabled,
        "next_run_at": c.next_run_at.isoformat() if c.next_run_at else None,
        "last_run_at": c.last_run_at.isoformat() if c.last_run_at else None,
        "created_at": c.created_at.isoformat(),
    }
    return out


def _with_last(session: Session, c: Connector) -> dict:
    last = _last_run(session, c.id)
    return {**_out(c), "last_run": _run(last) if last else None}


def _run(r: ConnectorRun, log: bool = False) -> dict:
    out = {
        "id": r.id,
        "status": r.status,
        "started_at": r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "stats": r.stats,
    }
    if log:
        out["log"] = r.log
    return out


def _get(session: Session, cid: UUID, *, lock: bool = False) -> Connector:
    c = session.get(Connector, cid, with_for_update=lock)
    if c is None:
        raise HTTPException(404, "not found")
    return c


def _last_run(session: Session, cid: UUID) -> ConnectorRun | None:
    return session.scalar(
        select(ConnectorRun).where(ConnectorRun.connector_id == cid).order_by(ConnectorRun.id.desc()).limit(1)
    )


def _name_free(session: Session, name: str, skip: UUID | None = None) -> None:
    taken = session.scalar(select(Connector.id).where(Connector.name == name))
    if taken is not None and taken != skip:
        raise HTTPException(409, "connector name already exists")


@router.get("/admin/connectors")
def list_connectors(p: Admin, session: Db) -> dict:
    cs = session.scalars(select(Connector).order_by(Connector.name)).all()
    return {"items": [_with_last(session, c) for c in cs]}


@router.post("/admin/connectors", status_code=201)
def create_connector(body: ConnectorIn, p: Admin, session: Db, request: Request) -> dict:
    org_unit = _check_scope(body.visibility, body.org_unit)
    config = _validate_config(body.kind, body.config.model_dump(), request)
    _name_free(session, body.name)
    c = Connector(
        name=body.name,
        kind=body.kind,
        config=config,
        owner_id=p.user_id,
        visibility=body.visibility,
        org_unit=org_unit,
        schedule_minutes=body.schedule_minutes,
        enabled=body.enabled,
    )
    session.add(c)
    try:
        session.flush()
    except IntegrityError:
        raise HTTPException(409, "connector name already exists") from None
    audit(session, p, "admin.connector_create", str(c.id), {"connector": _out(c)})
    return _out(c)


@router.get("/admin/connectors/{cid}")
def get_connector(cid: UUID, p: Admin, session: Db) -> dict:
    return _with_last(session, _get(session, cid))


@router.patch("/admin/connectors/{cid}")
def patch_connector(cid: UUID, body: ConnectorPatch, p: Admin, session: Db, request: Request) -> dict:
    c = _get(session, cid, lock=True)
    before = _out(c)
    sent = body.model_fields_set
    if body.name is not None:
        _name_free(session, body.name, skip=c.id)
        c.name = body.name
    if body.config is not None:
        new = dict(c.config)  # merge: missing keys keep their value, null deletes, "***" keeps the secret
        for k, v in body.config.items():
            if v is None:
                new.pop(k, None)
            elif not (k == "secret_key" and v == MASK):
                new[k] = v
        c.config = _validate_config(c.kind, new, request)
    elif c.kind == "fs":
        _validate_config("fs", c.config, request)  # roots may have changed since create
    visibility = body.visibility or c.visibility
    org_unit = _check_scope(visibility, body.org_unit if body.org_unit is not None else c.org_unit)
    if (visibility, org_unit) != (c.visibility, c.org_unit):  # existing documents follow the connector
        c.visibility, c.org_unit = visibility, org_unit
        ids = session.scalars(
            update(Document)
            .where(Document.connector_id == c.id)
            .values(visibility=visibility, org_unit=org_unit)
            .returning(Document.id)
        ).all()
        for doc_id in ids:
            enqueue(session, "acl_sync", {"doc_id": str(doc_id)})
    if "schedule_minutes" in sent:
        c.schedule_minutes, c.next_run_at = body.schedule_minutes, None  # due at the next tick
    if body.enabled is not None:
        c.enabled = body.enabled
    try:
        session.flush()
    except IntegrityError:
        raise HTTPException(409, "connector name already exists") from None
    audit(session, p, "admin.connector_update", str(c.id), {"before": before, "after": _out(c)})
    return _with_last(session, c)


@router.delete("/admin/connectors/{cid}", status_code=204)
def delete_connector(cid: UUID, p: Admin, session: Db) -> Response:
    c = _get(session, cid)
    audit(session, p, "admin.connector_delete", str(c.id), {"connector": _out(c)})
    session.delete(c)  # documents keep living (connector_id SET NULL); items and runs cascade
    session.flush()
    return Response(status_code=204)


@router.post("/admin/connectors/{cid}/run", status_code=202)
def run_connector(cid: UUID, p: Admin, session: Db) -> dict:
    c = _get(session, cid, lock=True)  # serializes concurrent triggers
    if active_run_job(session, c.id) is not None:
        raise HTTPException(409, "a run is already queued or running")
    return {"job_id": enqueue(session, "connector_run", {"connector_id": str(c.id)})}


@router.get("/admin/connectors/{cid}/runs")
def list_runs(
    cid: UUID, p: Admin, session: Db, paging: Annotated[tuple[int | None, int], Depends(keyset_params)]
) -> dict:
    _get(session, cid)
    before_id, limit = paging
    q = select(ConnectorRun).where(ConnectorRun.connector_id == cid)
    if before_id:
        q = q.where(ConnectorRun.id < before_id)
    rows = session.scalars(q.order_by(ConnectorRun.id.desc()).limit(limit + 1)).all()
    page = keyset_page([_run(r) for r in rows], limit)
    return page


@router.get("/admin/connectors/{cid}/runs/{run_id}")
def get_run(cid: UUID, run_id: int, p: Admin, session: Db) -> dict:
    r = session.get(ConnectorRun, run_id)
    if r is None or r.connector_id != cid:
        raise HTTPException(404, "not found")
    return _run(r, log=True)
