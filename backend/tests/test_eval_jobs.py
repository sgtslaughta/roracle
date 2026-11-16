import uuid

import pytest
from test_eval_runner import mkuser

from roracle.eval import jobs
from roracle.models_eval import EvalQuery, EvalSet
from roracle.queue import NonRetryable


def mkset(db, admin_only=False):
    u = mkuser(db, "u", admin=admin_only)
    s = EvalSet(name="ref", is_reference=True)
    db.add(s)
    db.flush()
    db.add(
        EvalQuery(
            set_id=s.id,
            key="q",
            text="t",
            kind="fact",
            principals=[u.id],
            relevant=[{"doc": str(uuid.uuid4())}],
        )
    )
    db.flush()
    return s


def call(db, payload, settings, **kw):
    jobs.handle_eval_run(db, payload, settings=settings, es="es", llm="llm", session_factory="sf")


def test_runs_stored_set_on_chat_path_with_judge(db_session, settings, monkeypatch):
    s = mkset(db_session)
    seen = {}
    monkeypatch.setattr(
        jobs.runner, "run_set", lambda session, es, llm, st, gs, **k: seen.update(k, gs=gs, es=es, llm=llm)
    )
    call(db_session, {"set_id": str(s.id), "trigger": "reindex", "judge": True}, settings)
    assert (seen["path"], seen["trigger"], seen["judge"], seen["sf"]) == ("chat", "reindex", True, "sf")
    assert [q.key for q in seen["gs"].queries] == ["q"] and (seen["es"], seen["llm"]) == ("es", "llm")


def test_unusable_jobs_do_not_retry(db_session, settings):
    with pytest.raises(NonRetryable, match="no longer exists"):
        call(db_session, {"set_id": str(uuid.uuid4()), "trigger": "reindex"}, settings)
    with pytest.raises(NonRetryable, match="non-admin"):
        call(
            db_session, {"set_id": str(mkset(db_session, admin_only=True).id), "trigger": "reindex"}, settings
        )
    with pytest.raises(NonRetryable, match="trigger"):
        call(db_session, {"set_id": str(uuid.uuid4()), "trigger": "cli"}, settings)
    with pytest.raises(NonRetryable):
        call(db_session, {"trigger": "reindex"}, settings)  # no set_id


def test_run_failure_is_not_retried(db_session, settings, monkeypatch):
    s = mkset(db_session)

    def boom(*a, **k):
        raise RuntimeError("judge down")

    monkeypatch.setattr(jobs.runner, "run_set", boom)
    with pytest.raises(NonRetryable, match="judge down"):
        call(db_session, {"set_id": str(s.id), "trigger": "reindex", "judge": True}, settings)
