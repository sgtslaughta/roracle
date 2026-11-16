"""Immutable per-request label policy: rank ordering, gate sets and effective-label maths."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.models_labels import GATES, Label


@dataclass(frozen=True)
class LabelInfo:
    id: str
    name: str
    colour: str
    description: str
    rank: int
    exclude_llm: bool
    block_agents: bool
    block_export: bool
    reason_on_lower: bool
    is_default: bool


def snapshot(row: Label) -> LabelInfo:
    return LabelInfo(
        id=str(row.id),
        name=row.name,
        colour=row.colour,
        description=row.description or "",
        rank=row.rank,
        exclude_llm=bool(row.exclude_llm),
        block_agents=bool(row.block_agents),
        block_export=bool(row.block_export),
        reason_on_lower=bool(row.reason_on_lower),
        is_default=bool(row.is_default),
    )


def label_json(label: LabelInfo) -> dict[str, Any]:
    return {
        "id": label.id,
        "name": label.name,
        "colour": label.colour,
        "rank": label.rank,
        "gates": {g: getattr(label, g) for g in GATES},
    }


def admin_json(label: LabelInfo) -> dict[str, Any]:
    return {**label_json(label), "description": label.description, "is_default": label.is_default}


class Policy:
    def __init__(self, labels: list[LabelInfo]):
        self.labels: tuple[LabelInfo, ...] = tuple(sorted(labels, key=lambda x: x.rank))
        self._by_id = {x.id: x for x in self.labels}

    @classmethod
    def load(cls, session: Session) -> "Policy":
        return cls([snapshot(r) for r in session.scalars(select(Label))])

    def ids(self, gate: str) -> frozenset[str]:
        if gate not in GATES:
            raise ValueError(f"unknown gate {gate!r}")
        return frozenset(x.id for x in self.labels if getattr(x, gate))

    def get(self, label_id: str | uuid.UUID) -> LabelInfo:
        return self._by_id[str(label_id)]

    def has(self, label_id: str | uuid.UUID, gate: str) -> bool:
        return getattr(self.get(label_id), gate)

    def effective(self, doc_label_id: str | uuid.UUID, note_label_id: str | uuid.UUID | None) -> LabelInfo:
        doc = self.get(doc_label_id)
        if note_label_id is None:
            return doc
        note = self.get(note_label_id)
        return note if note.rank > doc.rank else doc

    @property
    def default(self) -> LabelInfo:
        return next(x for x in self.labels if x.is_default)
