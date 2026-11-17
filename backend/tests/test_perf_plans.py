"""Plan-shape tests (deterministic): the hot queries use their indexes at scale. Seeds 200k jobs."""

import pytest
from sqlalchemy import select, text

from roracle.models import Job
from roracle.perf.explain import has_node, index_names, plan, seq_scans
from roracle.queue import _BULK_ONLY, _USER_ONLY, claim_stmt

pytestmark = pytest.mark.integration


@pytest.fixture
def big_queue(db_session):
    db_session.execute(
        text(
            "INSERT INTO jobs (kind, payload, status, run_after) SELECT"
            " CASE WHEN i % 3 = 0 THEN 'rebuild_units' ELSE 'ingest' END, '{}', 'queued',"
            " now() - (200000 - i) * interval '1 second' FROM generate_series(1, 200000) i"
        )
    )
    db_session.execute(
        text(
            "INSERT INTO jobs (kind, payload, status, updated_at) SELECT 'ingest', '{}', 'done',"
            " now() - interval '1 hour' FROM generate_series(1, 20000)"
        )
    )
    db_session.commit()
    db_session.execute(text("ANALYZE jobs"))
    db_session.commit()
    yield db_session
    db_session.rollback()
    db_session.execute(text("TRUNCATE jobs RESTART IDENTITY"))
    db_session.commit()


def test_claim_uses_partial_indexes_without_sort(big_queue):
    p = plan(big_queue, claim_stmt(["ingest"], _USER_ONLY))
    assert "ix_jobs_due_user" in index_names(p)
    assert not has_node(p, "Sort") and "jobs" not in seq_scans(p)
    p = plan(big_queue, claim_stmt(["rebuild_units"], _BULK_ONLY))
    assert "ix_jobs_due_bulk" in index_names(p) and not has_node(p, "Sort")
    big_queue.rollback()


def test_scheduled_uses_kind_status_index(big_queue):
    p = plan(
        big_queue,
        select(Job.id)
        .where(Job.kind == "maintenance", Job.payload["op"].astext == "all", Job.status.in_(("queued",)))
        .limit(1),
    )
    assert "ix_jobs_kind_status" in index_names(p) and "jobs" not in seq_scans(p)
    big_queue.rollback()
