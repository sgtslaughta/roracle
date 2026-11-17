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


def test_cited_by_counts_is_scoped_to_the_requested_ids(db_session, settings):
    """40k docs / 200k citations: the window reads only the groups citing the 20 ids, never the table."""
    from roracle.auth import Principal
    from roracle.citations import cited_by_counts_stmt
    from roracle.perf import synth_pg
    from roracle.perf.explain import nodes

    synth_pg.seed(settings.database_url, docs=40_000, users=100)
    p = Principal(user_id=synth_pg.det_uuid(1, "u", 0), sub="s", username="u", groups=(), is_admin=False)
    ids = [synth_pg.det_uuid(1, "d", i) for i in range(0, 4000, 200)]
    pl = plan(db_session, cited_by_counts_stmt(p, ids))
    db_session.rollback()
    assert "doc_citations" not in seq_scans(pl)
    n_cites = db_session.scalar(
        text("SELECT count(*) FROM doc_citations WHERE target_document_id = ANY(:i)"), {"i": ids}
    )
    for n in nodes(pl):
        if n.get("Node Type") == "WindowAgg":
            fed = n["Plans"][0]["Actual Rows"] * n["Plans"][0]["Actual Loops"]
            assert fed <= 10 * max(n_cites, 1), (fed, n_cites)
    db_session.execute(text("TRUNCATE users, documents, jobs, blobs RESTART IDENTITY CASCADE"))
    db_session.commit()


def test_hot_path_plans_at_200k_docs(db_session, settings):
    """Browse, tag filter, shares lookup and the label.view audit lookup all use their new indexes."""
    from datetime import UTC, datetime

    from roracle.api.routes.search import load_stmt
    from roracle.auth import Principal
    from roracle.labels.policy import label_view_seen_stmt
    from roracle.models import DocumentShare
    from roracle.perf import synth_pg

    synth_pg.seed(settings.database_url, docs=200_000, users=200, audit_rows=200_000)
    db_session.execute(text("ANALYZE"))
    db_session.commit()
    uid = synth_pg.det_uuid(1, "u", 7)
    p = Principal(user_id=uid, sub="s", username="u", groups=("g3",), is_admin=False)

    browse = plan(db_session, load_stmt(p, None, None, 200))
    assert "document_versions" not in seq_scans(browse) and not has_node(browse, "Sort")
    assert "ix_versions_latest_searchable" in index_names(browse)
    db_session.rollback()

    tagged = plan(db_session, load_stmt(p, None, {"tags": {"tag7"}}, 200))
    assert "ix_tags_tag" in index_names(tagged) and "tags" not in seq_scans(tagged)
    db_session.rollback()

    shares = plan(
        db_session,
        select(DocumentShare.document_id).where(
            DocumentShare.principal_kind == "user", DocumentShare.principal == str(uid)
        ),
    )
    assert "ix_document_shares_principal" in index_names(shares) and "document_shares" not in seq_scans(
        shares
    )
    db_session.rollback()

    day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    audit = plan(db_session, label_view_seen_stmt(uid, "target-5", day))
    assert "ix_audit_label_view" in index_names(audit) and "audit_log" not in seq_scans(audit)
    db_session.rollback()
    db_session.execute(text("TRUNCATE users, documents, jobs, blobs, audit_log RESTART IDENTITY CASCADE"))
    db_session.commit()
