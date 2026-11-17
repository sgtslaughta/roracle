"""Head-chunk signals (votes, global cited-by, searchable): PG counts and the job producers."""

from api_seed import seed
from sqlalchemy import select

from roracle.citations import handle_citations
from roracle.models import DocCitation, Job, Vote
from roracle.search_signals import enqueue_signals, signals_for


def _jobs(db):
    return [j.payload for j in db.scalars(select(Job).where(Job.kind == "search_signals"))]


def test_signals_counts(db_session, ana, bob):
    d, v = seed(db_session, ana)
    _, cv1 = seed(db_session, bob, visibility="private")
    _, cv2 = seed(db_session, bob)
    db_session.add_all(
        [
            DocCitation(src_version_id=cv1.id, ref_no=1, target_document_id=d.id, confidence=1.0),
            DocCitation(src_version_id=cv2.id, ref_no=1, target_document_id=d.id, confidence=0.93),
            DocCitation(src_version_id=cv2.id, ref_no=2, target_document_id=d.id, confidence=1.0),
            Vote(user_id=ana, document_id=d.id, value=1),
            Vote(user_id=bob, document_id=d.id, value=-1),
        ]
    )
    db_session.flush()
    assert signals_for(db_session, [d.id])[str(v.id)] == {
        "votes_net": 0,
        "cited_by_global": 2,
        "searchable": True,
    }


def test_signals_per_version_and_unsearchable_citer(db_session, ana):
    d, _ = seed(db_session, ana, versions=2, stage="chunked")
    _, cv = seed(db_session, ana, stage="chunked")
    db_session.add(DocCitation(src_version_id=cv.id, ref_no=1, target_document_id=d.id, confidence=1.0))
    db_session.flush()
    out = signals_for(db_session, [d.id])
    assert len(out) == 2 and all(
        s == {"votes_net": 0, "cited_by_global": 0, "searchable": False} for s in out.values()
    )


def test_enqueue_empty_noop_and_sorted(db_session, ana):
    enqueue_signals(db_session, [])
    assert _jobs(db_session) == []
    d, _ = seed(db_session, ana)
    enqueue_signals(db_session, [d.id, d.id])
    assert _jobs(db_session) == [{"doc_ids": [str(d.id)]}]


def test_vote_enqueues_signals(client, as_ana, ana, db_session):
    d, _ = seed(db_session, ana)
    db_session.flush()
    r = client.put(f"/documents/{d.id}/vote", headers=as_ana, json={"value": 1})
    assert r.status_code == 200
    assert _jobs(db_session) == [{"doc_ids": [str(d.id)]}]


def test_citations_job_enqueues_old_and_new_targets(db_session, ana):
    a, _ = seed(db_session, ana, title="Old Target Paper", year=2019)
    b, _ = seed(db_session, ana, title="New Target Paper", year=2020)
    c, cv = seed(db_session, ana, title="Citing paper")
    cv.references = [{"title": "New Target Paper", "year": 2020}]
    db_session.add(DocCitation(src_version_id=cv.id, ref_no=1, target_document_id=a.id, confidence=1.0))
    db_session.flush()
    handle_citations(db_session, {"version_id": str(cv.id)})
    assert _jobs(db_session) == [{"doc_ids": sorted(map(str, [a.id, b.id, c.id]))}]
