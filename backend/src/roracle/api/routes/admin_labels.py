import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from roracle.api.routes.admin_deps import Admin
from roracle.audit import audit
from roracle.db import get_session
from roracle.labels import admin
from roracle.labels.policy import admin_json, snapshot

router = APIRouter()
Db = Annotated[Session, Depends(get_session)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Rank = Annotated[int, Field(ge=0, le=10_000)]
Colour = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class GatesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exclude_llm: bool = False
    block_agents: bool = False
    block_export: bool = False
    reason_on_lower: bool = False


class LabelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    colour: Colour
    description: str = Field("", max_length=500)
    rank: Rank
    gates: GatesIn = GatesIn()
    is_default: bool = False


class LabelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    colour: Colour | None = None
    description: str | None = Field(None, max_length=500)
    rank: Rank | None = None
    gates: GatesIn | None = None
    is_default: bool | None = None


class OrderIn(BaseModel):
    ids: list[uuid.UUID]


@router.post("/admin/labels", status_code=201)
def create_label(body: LabelIn, p: Admin, session: Db) -> dict:
    row = admin.create(session, body.model_dump())
    audit(session, p, "label.create", str(row.id), {"name": row.name, "rank": row.rank})
    return admin_json(snapshot(row))


@router.patch("/admin/labels/{label_id}")
def patch_label(label_id: uuid.UUID, body: LabelPatch, p: Admin, session: Db) -> dict:
    data = body.model_dump(exclude_none=True, exclude={"gates"})
    if body.gates:  # only the gates the request names
        data["gates"] = body.gates.model_dump(include=body.gates.model_fields_set)
    row, changes = admin.update(session, label_id, data)
    audit(session, p, "label.update", str(row.id), {"name": row.name, "changes": changes})
    return admin_json(snapshot(row))


@router.delete("/admin/labels/{label_id}", status_code=204)
def delete_label(label_id: uuid.UUID, p: Admin, session: Db) -> Response:
    row = admin.delete(session, label_id)
    audit(session, p, "label.delete", str(label_id), {"name": row.name})
    return Response(status_code=204)


@router.put("/admin/labels/order")
def order_labels(body: OrderIn, p: Admin, session: Db) -> dict:
    rows = admin.reorder(session, body.ids)
    audit(session, p, "label.reorder", "/admin/labels/order", {"ids": [str(i) for i in body.ids]})
    return {"items": [admin_json(snapshot(r)) for r in rows]}
