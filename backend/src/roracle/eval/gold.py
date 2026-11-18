"""Gold-set schema (fixture files and Postgres sets) and principal resolution.

In files, principal/doc refs are keys; in Postgres they are UUID strings.
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.auth import Principal, principal_for_user
from roracle.models_eval import EvalQuery, EvalSet

PAT = ":pat"
R5_ERROR = "evaluate as at least one non-admin principal"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoldItem(_Strict):
    doc: str
    quote: str | None = None
    page: int | None = Field(default=None, ge=1)
    grade: Literal[1, 2, 3] = 3


class GoldQuery(_Strict):
    key: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=2000)
    kind: Literal["fact", "multihop", "global"]
    principals: list[str] = Field(min_length=1)
    as_pat: bool = False
    relevant: list[GoldItem] = Field(min_length=1)
    reference_answer: str | None = None


class GoldSet(_Strict):
    name: str = ""
    queries: list[GoldQuery]


class FixPrincipal(_Strict):
    groups: list[str] = []
    admin: bool = False


class FixLabel(_Strict):
    name: str
    exclude_llm: bool = False
    block_agents: bool = False


class FixChunk(_Strict):
    text: str
    headings: list[str] = []
    pages: list[int] = [1]


class FixDoc(_Strict):
    key: str
    title: str
    owner: str
    visibility: Literal["private", "shared", "org_unit", "everyone"]
    org_unit: str | None = None
    shares: list[str] = []
    label: str | None = None
    tags: list[str] = []  # denormalized onto every chunk (normalized tag names)
    abstract: str | None = None  # version meta: lands on the head chunk's `abstract` field
    chunks: list[FixChunk] = Field(min_length=1)


class FixtureSet(GoldSet):
    principals: dict[str, FixPrincipal]
    labels: list[FixLabel] = []
    documents: list[FixDoc]

    @model_validator(mode="after")
    def _refs(self) -> "FixtureSet":
        docs = {d.key for d in self.documents}
        labels = {x.name for x in self.labels}
        bad = [d.key for d in self.documents if d.label and d.label not in labels]
        bad += [d.key for d in self.documents for r in (d.owner, *d.shares) if r not in self.principals]
        bad += [q.key for q in self.queries for r in q.principals if r not in self.principals]
        bad += [q.key for q in self.queries for g in q.relevant if g.doc not in docs]
        if bad:
            raise ValueError(f"unknown references in: {sorted(set(bad))}")
        return self


def load_file(path: str | Path) -> FixtureSet:
    path = Path(path)
    raw = path.read_text()
    data = json.loads(raw) if path.suffix == ".json" else yaml.safe_load(raw)
    return FixtureSet.model_validate(data)


def _item(i: dict) -> GoldItem:
    g = GoldItem.model_validate(i)
    try:
        g.doc = str(UUID(g.doc))  # canonical form: hits carry lowercase hyphenated ids
    except ValueError:
        pass
    return g


def load_db(session: Session, set_id: UUID) -> GoldSet:
    """Active queries only (drafts wait for admin review)."""
    es = session.get(EvalSet, set_id)
    if es is None:
        raise LookupError(f"eval set {set_id} not found")
    rows = session.scalars(
        select(EvalQuery)
        .where(EvalQuery.set_id == set_id, EvalQuery.status == "active")
        .order_by(EvalQuery.key)
    )
    return GoldSet(
        name=es.name,
        queries=[
            GoldQuery(
                key=r.key,
                text=r.text,
                kind=r.kind,
                principals=[str(u) for u in r.principals],
                as_pat=r.as_pat,
                relevant=[_item(i) for i in r.relevant],
                reference_answer=r.reference_answer,
            )
            for r in rows
        ],
    )


def resolve_principals(session: Session, gs: GoldSet) -> dict[str, Principal]:
    """ref -> Principal (via `principal_for_user`); `ref:pat` -> read-only PAT variant for `as_pat` queries.

    R5: refuses a set whose principals are all admins (admins see everything, so nothing would be tested).
    """
    out: dict[str, Principal] = {}
    for q in gs.queries:
        for ref in q.principals:
            if ref not in out:
                try:
                    out[ref] = principal_for_user(session, UUID(ref))
                except (ValueError, LookupError) as e:
                    raise ValueError(f"unknown principal {ref}") from e
            if q.as_pat:
                out[ref + PAT] = replace(out[ref], via="pat", scopes=frozenset({"read"}))
    if not any(not p.is_admin for p in out.values()):
        raise ValueError(R5_ERROR)
    return out
