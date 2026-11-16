"""Admin label CRUD: validation of the whole label set plus writes (all audited by the route layer)."""

import re
import uuid
from collections.abc import Sequence
from dataclasses import replace

from fastapi import HTTPException
from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from roracle.labels.policy import LabelInfo, snapshot
from roracle.models import Annotation, Connector, Document
from roracle.models_labels import GATES, MAX_LABELS, Label

COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _422(msg: str) -> HTTPException:
    return HTTPException(422, msg)


def validate_set(labels: Sequence[LabelInfo]) -> None:
    """Whole-set invariants; raises 422 `{"detail": ...}`."""
    if len(labels) > MAX_LABELS:
        raise _422(f"at most {MAX_LABELS} labels")
    for x in labels:
        if not 1 <= len(x.name) <= 40:
            raise _422("name must be 1-40 characters")
        if not COLOUR.match(x.colour):
            raise _422("colour must be #rrggbb")
        if len(x.description) > 500:
            raise _422("description too long")
    if len({x.name.lower() for x in labels}) != len(labels):
        raise _422("duplicate label name")
    if len({x.rank for x in labels}) != len(labels):
        raise _422("duplicate rank")
    if sum(x.is_default for x in labels) != 1:
        raise _422("exactly one default label required")
    ordered = sorted(labels, key=lambda x: x.rank)
    for g in GATES:
        seen = False
        for x in ordered:
            if seen and not getattr(x, g):
                raise _422("gates must not decrease with rank")
            seen = seen or getattr(x, g)


def _lock(session: Session) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('labels', 0))"))


def _current(session: Session) -> list[Label]:
    return list(session.scalars(select(Label)))


def _fields(body: dict) -> dict:
    gates = body.pop("gates", None) or {}
    return {**body, **gates}


def create(session: Session, body: dict) -> Label:
    _lock(session)
    rows = _current(session)
    fields = _fields(dict(body))
    row = Label(id=uuid.uuid4(), **fields)
    proposed = [snapshot(r) for r in rows]
    if row.is_default:
        proposed = [replace(x, is_default=False) for x in proposed]
    proposed.append(snapshot(_filled(row)))
    validate_set(proposed)
    if row.is_default:
        _unset_default(session, rows)
    session.add(row)
    session.flush()
    return row


def _filled(row: Label) -> Label:
    for k in ("description", *GATES, "is_default"):
        if getattr(row, k, None) is None:
            setattr(row, k, "" if k == "description" else False)
    return row


def _unset_default(session: Session, rows: list[Label]) -> None:
    for r in rows:
        if r.is_default:
            r.is_default = False
    session.flush()


def update(session: Session, label_id: uuid.UUID, body: dict) -> tuple[Label, dict]:
    """Returns (row, {field: {"old","new"}})."""
    _lock(session)
    rows = _current(session)
    row = next((r for r in rows if r.id == label_id), None)
    if row is None:
        raise HTTPException(404, "not found")
    fields = _fields(dict(body))
    proposed = []
    for r in rows:
        info = snapshot(r)
        if r is row:
            info = replace(info, **fields)
        elif fields.get("is_default"):
            info = replace(info, is_default=False)
        proposed.append(info)
    validate_set(proposed)
    changes = {k: {"old": getattr(row, k), "new": v} for k, v in fields.items() if getattr(row, k) != v}
    if fields.get("is_default") and not row.is_default:
        _unset_default(session, rows)
    for k, v in fields.items():
        setattr(row, k, v)
    session.flush()
    return row, changes


def delete(session: Session, label_id: uuid.UUID) -> Label:
    _lock(session)
    row = session.get(Label, label_id)
    if row is None:
        raise HTTPException(404, "not found")
    used = session.scalar(
        select(
            exists().where(Document.label_id == label_id)
            | exists().where(Annotation.label_id == label_id)
            | exists().where(Connector.label_id == label_id)
        )
    )
    if row.is_default or used:
        raise HTTPException(409, "label in use")
    session.delete(row)
    session.flush()
    return row


def reorder(session: Session, ids: list[uuid.UUID]) -> list[Label]:
    """`ids` lists every label exactly once, least sensitive first; ranks become 0..n-1."""
    _lock(session)
    rows = _current(session)
    if len(set(ids)) != len(ids) or {r.id for r in rows} != set(ids):
        raise _422("ids must list every label exactly once")
    by_id = {r.id: r for r in rows}
    proposed = [replace(snapshot(by_id[i]), rank=n) for n, i in enumerate(ids)]
    validate_set(proposed)
    for n, i in enumerate(ids):
        by_id[i].rank = n  # uq_labels_rank is DEFERRABLE INITIALLY DEFERRED: swaps are fine
    session.flush()
    return [by_id[i] for i in ids]
