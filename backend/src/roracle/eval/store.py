"""Eval run persistence."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from roracle.eval.metrics import PairResult
from roracle.models_eval import EvalResult, EvalRun, EvalSet


def set_by_name(session: Session, name: str) -> EvalSet | None:
    return session.scalar(select(EvalSet).where(EvalSet.name == name))


def start_run(
    session: Session, *, set_id: UUID | None, set_name: str, trigger: str, label: str, path: str, config: dict
) -> UUID:
    run = EvalRun(set_id=set_id, set_name=set_name, trigger=trigger, label=label, path=path, config=config)
    session.add(run)
    session.commit()
    return run.id


def add_result(
    session: Session, run_id: UUID, r: PairResult, ranked: list[list[str]], judge: Any = None
) -> None:
    session.add(
        EvalResult(
            run_id=run_id,
            query_key=r.query_key,
            principal=r.principal,
            kind=r.kind,
            metrics=r.scores,
            ranked=ranked,
            judge=judge,
            latency_ms=r.latency_ms,
        )
    )


def finish_run(session: Session, run_id: UUID, metrics: dict | None, error: str | None = None) -> str:
    run = session.get(EvalRun, run_id)
    run.metrics, run.error, run.finished_at = metrics, error, datetime.now(UTC)
    run.status = "failed" if error else "done"
    session.commit()
    return run.status


@dataclass
class PairView:
    query_key: str
    principal: str  # user id, or "<id>:pat"
    kind: str
    scores: dict[str, float] | None  # None: empty expected set
    judge: dict[str, Any] | None = None


@dataclass
class RunView:
    """Read model of a finished run for the gate and the reports."""

    id: str
    set_name: str
    path: str
    label: str
    config: dict[str, Any]
    metrics: dict[str, Any]
    pairs: list[PairView] = field(default_factory=list)


def load_run(session: Session, run_id: UUID) -> RunView:
    run = session.get(EvalRun, run_id)
    if run is None or run.metrics is None:
        raise LookupError(f"eval run {run_id} has no metrics")
    rows = session.scalars(select(EvalResult).where(EvalResult.run_id == run_id))
    pairs = [PairView(r.query_key, r.principal, r.kind, r.metrics, r.judge) for r in rows]
    return RunView(str(run.id), run.set_name, run.path, run.label, run.config, run.metrics, pairs)
