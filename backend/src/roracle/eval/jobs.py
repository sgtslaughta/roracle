"""Queue handler `eval_run`: payload `{set_id, trigger, judge}` runs a stored set on the chat path."""

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from roracle import runtime
from roracle.config import Settings
from roracle.eval import gold, runner
from roracle.models_eval import EvalSet
from roracle.queue import NonRetryable

TRIGGERS = ("admin", "model_change", "reindex")


def handle_eval_run(
    session: Session, payload: dict, *, settings: Settings, es, llm, session_factory: sessionmaker[Session]
) -> None:
    """Run rows are written through `session_factory` (own transactions), so a failed run is kept."""
    trigger = payload.get("trigger", "reindex")
    if trigger not in TRIGGERS:
        raise NonRetryable(f"unknown eval trigger {trigger!r}")
    try:
        es_set = session.get(EvalSet, UUID(payload["set_id"]))
        if es_set is None:
            raise NonRetryable("eval set no longer exists")
        gs = gold.load_db(session, es_set.id)
        gold.resolve_principals(session, gs)  # R5 / unknown principals: retrying cannot help
    except (ValueError, KeyError) as e:
        raise NonRetryable(str(e)) from e
    try:
        runner.run_set(
            session, es, llm, runtime.effective(session, settings), gs,
            path="chat", trigger=trigger, label=trigger, judge=bool(payload.get("judge")), sf=session_factory,
        )  # fmt: skip
    except Exception as e:  # the failed run row is stored; a retry would repeat a whole judged run
        raise NonRetryable(f"eval run failed: {type(e).__name__}: {e}") from e
