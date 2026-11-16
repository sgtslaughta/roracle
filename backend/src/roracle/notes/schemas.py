from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

BODY_MAX = 10_000
SHARES_MAX = 50


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float
    coord_origin: str | None = None


class Anchor(BaseModel):
    quote: str = Field(min_length=1, max_length=1000)
    prefix: str = Field("", max_length=200)
    suffix: str = Field("", max_length=200)
    page: int | None = Field(None, ge=1)
    bbox: BBox | None = None


class Relation(BaseModel):
    type: Literal["extends", "contradicts", "replicates", "supersedes"]
    target_document_id: UUID


class Share(BaseModel):
    kind: Literal["user", "group"]
    principal: str = Field(min_length=1, max_length=500)


Visibility = Literal["private", "shared", "everyone"]


class NoteIn(BaseModel):
    kind: Literal["comment", "finding", "correction", "question", "relation"] = "comment"
    body: str = Field(min_length=1, max_length=BODY_MAX)
    anchor: Anchor | None = None
    relation: Relation | None = None
    visibility: Visibility | None = None
    shares: list[Share] = Field(default_factory=list, max_length=SHARES_MAX)

    @model_validator(mode="after")
    def _rules(self):
        if (self.kind == "relation") != (self.relation is not None):
            raise ValueError("relation is required for, and only for, kind 'relation'")
        return self


class NotePatch(BaseModel):
    body: str | None = Field(None, min_length=1, max_length=BODY_MAX)
    visibility: Visibility | None = None
    shares: list[Share] | None = Field(None, max_length=SHARES_MAX)


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=BODY_MAX)
