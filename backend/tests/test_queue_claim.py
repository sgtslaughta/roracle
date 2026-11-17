from sqlalchemy import func, select, text

from roracle import maintenance
from roracle.models import Job
from roracle.queue import claim

KINDS = ["ingest", "rebuild_units"]


def _add(db, kind, *, age_s=0, status="queued"):
    j = Job(kind=kind, payload={}, status=status)
    db.add(j)
    db.flush()
    db.execute(
        text("UPDATE jobs SET run_after = now() - make_interval(secs => :a) WHERE id = :i"),
        {"a": age_s, "i": j.id},
    )
    return j.id


def test_bulk_never_wins_over_due_user_job(db_session):
    bulk = _add(db_session, "rebuild_units", age_s=1000)  # older than the user job
    user = _add(db_session, "ingest", age_s=10)
    db_session.commit()
    assert claim(db_session, KINDS).id == user
    assert claim(db_session, KINDS).id == bulk  # bulk only once no user job is due
    assert claim(db_session, KINDS) is None


def test_ties_are_fifo_by_id(db_session):
    ids = [_add(db_session, "ingest") for _ in range(3)]
    db_session.execute(text("UPDATE jobs SET run_after = now() - interval '1 hour'"))
    db_session.commit()
    assert [claim(db_session, KINDS).id for _ in range(3)] == ids


def test_oldest_run_after_first_and_future_jobs_wait(db_session):
    new = _add(db_session, "ingest", age_s=5)
    old = _add(db_session, "ingest", age_s=500)
    _add(db_session, "ingest", age_s=-3600)  # not due
    db_session.commit()
    assert [claim(db_session, KINDS).id, claim(db_session, KINDS).id] == [old, new]
    assert claim(db_session, KINDS) is None


def test_claim_without_bulk_handler_ignores_bulk(db_session):
    _add(db_session, "rebuild_units")
    db_session.commit()
    assert claim(db_session, ["ingest"]) is None
    assert claim(db_session, ["rebuild_units"]) is not None  # bulk-only worker still works


def test_prune_jobs_keeps_queued_running_and_recent(db_session):
    keep = [_add(db_session, "ingest", status=s) for s in ("queued", "running")]
    old = [_add(db_session, "ingest", status="done"), _add(db_session, "ingest", status="failed")]
    fresh_done = _add(db_session, "ingest", status="done")
    mid_failed = _add(db_session, "ingest", status="failed")
    db_session.execute(
        text("UPDATE jobs SET updated_at = now() - interval '40 days' WHERE id = ANY(:i)"),
        {"i": [*keep, *old]},
    )
    db_session.execute(
        text("UPDATE jobs SET updated_at = now() - interval '10 days' WHERE id = :i"), {"i": mid_failed}
    )
    assert maintenance.prune_jobs(db_session) == 2
    left = set(db_session.scalars(select(Job.id)))
    assert left == {*keep, fresh_done, mid_failed}


def test_prune_jobs_batches_and_reports_more(db_session):
    for _ in range(5):
        _add(db_session, "ingest", status="done")
    db_session.execute(text("UPDATE jobs SET updated_at = now() - interval '8 days'"))
    assert maintenance.prune_jobs(db_session, batch=3) == 3
    assert maintenance.prune_jobs(db_session, batch=3) == 2
    assert db_session.scalar(select(func.count()).select_from(Job)) == 0


def test_depth_metric_ignores_done_rows(db_session):
    from roracle.metrics import _DEPTH_SQL

    _add(db_session, "ingest")
    _add(db_session, "ingest", status="done")
    _add(db_session, "ingest", status="failed")
    rows = {(k, s): n for k, s, n, _ in db_session.execute(_DEPTH_SQL)}
    assert rows == {("ingest", "queued"): 1, ("ingest", "failed"): 1}
