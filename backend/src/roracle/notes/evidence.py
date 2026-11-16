"""Evidence: RO-Crate provenance, scan job and JSON shape (P8 task 5; R7)."""

import json
import re
from collections import Counter
from contextlib import closing
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

from botocore.exceptions import ClientError
from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.config import Settings
from roracle.ingest.scan import scan
from roracle.models import Evidence, EvidenceReproduction
from roracle.queue import NonRetryable
from roracle.storage import BlobStore

CONTEXT = "https://w3id.org/ro/crate/1.1/context"  # identifier only, never fetched (air-gapped)
META_MAX = 256 * 1024
PARAMS_MAX = 64 * 1024
URI_RE = re.compile(r"^(https?|s3|doi|git|ssh):", re.IGNORECASE)
GIT_SCP = re.compile(r"^[\w.-]+@[\w.-]+:\S+$")


def _uri(v: str | None) -> str | None:
    if v is not None and not URI_RE.match(v):
        raise ValueError("uri scheme not allowed")
    return v


def _no_at_keys(v: Any) -> None:
    if isinstance(v, dict):
        for k, x in v.items():
            if str(k).startswith("@"):
                raise ValueError("parameter keys must not start with '@'")
            _no_at_keys(x)
    elif isinstance(v, list):
        for x in v:
            _no_at_keys(x)


Hex64 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Code(BaseModel):
    repo: str = Field(min_length=1, max_length=2000)
    commit: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")

    @field_validator("repo")
    @classmethod
    def _repo(cls, v: str) -> str:
        return v if GIT_SCP.match(v) else _uri(v)  # type: ignore[return-value]


class Env(BaseModel):
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class Item(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    uri: str | None = Field(None, max_length=2000)
    sha256: Hex64 | None = None

    _u = field_validator("uri")(_uri)


class AgentInfo(BaseModel):
    client: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)


class Provenance(BaseModel):
    code: Code
    environment: Env
    inputs: list[Item] = Field(max_length=100)
    outputs: list[Item] = Field(max_length=100)
    parameters: dict[str, Any]
    seed: int | str | None
    agent: AgentInfo
    started_at: AwareDatetime
    ended_at: AwareDatetime
    description: str = Field("", max_length=5000)
    uri: str | None = Field(None, max_length=2000)
    uri_sha256: Hex64 | None = None

    @field_validator("parameters")
    @classmethod
    def _params(cls, v: dict) -> dict:
        if len(json.dumps(v)) > PARAMS_MAX:
            raise ValueError("parameters exceed 64 KB")
        _no_at_keys(v)
        return v

    _u = field_validator("uri")(_uri)

    @model_validator(mode="after")
    def _order(self) -> "Provenance":
        if self.started_at > self.ended_at:
            raise ValueError("started_at must not be after ended_at")
        if (self.uri is None) != (self.uri_sha256 is None):
            raise ValueError("uri and uri_sha256 must be given together")
        return self


class EvidenceMeta(Provenance):
    stance: Literal["supports", "refutes", "inconclusive"]


def _items(xs: list[Item]) -> list[dict]:
    return [
        {
            "@type": "File",
            "name": i.name,
            **({"url": i.uri} if i.uri else {}),
            **({"sha256": i.sha256} if i.sha256 else {}),
        }
        for i in xs
    ]


def crate(p: Provenance) -> dict:
    """RO-Crate 1.1 JSON-LD for one run (CreateAction with instrument SoftwareSourceCode)."""
    code = {
        "@id": "#code",
        "@type": "SoftwareSourceCode",
        "codeRepository": p.code.repo,
        "version": p.code.commit,
    }
    action: dict[str, Any] = {
        "@id": "#run",
        "@type": "CreateAction",
        "name": "evidence run",
        "instrument": {"@id": "#code"},
        "agent": {"@id": "#agent"},
        "startTime": p.started_at.isoformat(),
        "endTime": p.ended_at.isoformat(),
        "object": _items(p.inputs),
        "result": _items(p.outputs),
        "environment": {
            "@type": "PropertyValue",
            "name": "image_digest",
            "value": p.environment.image_digest,
        },
        "parameters": p.parameters,
        "seed": p.seed,
    }
    root: dict[str, Any] = {
        "@id": "./",
        "@type": "Dataset",
        "name": "rOracle evidence",
        "description": p.description,
    }
    if p.uri:
        root["url"] = p.uri
        if p.uri_sha256:
            root["sha256"] = p.uri_sha256
    return {
        "@context": CONTEXT,
        "@graph": [
            {
                "@id": "ro-crate-metadata.json",
                "@type": "CreativeWork",
                "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
                "about": {"@id": "./"},
            },
            root,
            action,
            code,
            {
                "@id": "#agent",
                "@type": "SoftwareApplication",
                "name": p.agent.client,
                "version": p.agent.model,
            },
        ],
    }


def handle_evidence_scan(session: Session, payload: dict, *, settings: Settings, store: BlobStore) -> None:
    """Scan an evidence blob. Never commits; ConnectionError propagates for retry."""
    e = session.get(Evidence, UUID(payload["evidence_id"]))
    if e is None or e.blob_sha256 is None:
        return
    if not settings.clamav_host:
        e.scan_status = "unscanned"
        return
    try:
        with closing(store.open(e.blob_sha256)) as body:
            sig = scan(body, settings.clamav_host, port=settings.clamav_port)
    except NonRetryable:
        e.scan_status = "failed"
        return
    except ClientError as ex:
        if ex.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404", "NotFound"):
            raise
        e.scan_status = "failed"
        return
    e.scan_status = "infected" if sig else "clean"


def safe_filename(name: str) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[\x00-\x1f\x7f\"]", "", base).strip(". ")[:200]
    return base or "evidence"


def content_disposition(name: str) -> str:
    ascii_name = re.sub(r"[^\x20-\x7e]|[\;]", "_", name)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


def to_json(session: Session, p, rows: list[Evidence]) -> list[dict]:
    ids = [e.id for e in rows]
    counts: Counter = Counter()
    mine: dict[UUID, str] = {}
    if ids:
        for eid, outcome, uid in session.execute(
            select(
                EvidenceReproduction.evidence_id, EvidenceReproduction.outcome, EvidenceReproduction.user_id
            ).where(EvidenceReproduction.evidence_id.in_(ids))
        ):
            counts[(eid, outcome)] += 1
            if uid == p.user_id:
                mine[eid] = outcome
    return [
        {
            "id": str(e.id),
            "annotation_id": str(e.annotation_id),
            "stance": e.stance,
            "crate": e.crate,
            "file": {
                "filename": e.filename,
                "size": e.size,
                "sha256": e.blob_sha256,
                "scan_status": e.scan_status,
            }
            if e.blob_sha256
            else None,
            "uri": e.uri,
            "uri_sha256": e.uri_sha256,
            "reproductions": {
                "reproduced": counts[(e.id, "reproduced")],
                "failed": counts[(e.id, "failed")],
                "mine": mine.get(e.id),
            },
            "created_by": str(e.creator_id),
            "created_at": e.created_at.isoformat() if isinstance(e.created_at, datetime) else None,
        }
        for e in rows
    ]
