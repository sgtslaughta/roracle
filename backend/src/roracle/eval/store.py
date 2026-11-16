"""Eval run persistence."""

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
