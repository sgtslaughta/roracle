import pytest
from api_seed import seed
from sqlalchemy import select

from roracle.citations import backfill_for, handle_citations, resolve_references
from roracle.models import DocCitation


def lib(db, owner, title, *, year=2020, doi=None, **kw):
    d, v = seed(db, owner, title=title, year=year, **kw)
    v.meta = {**v.meta, "doi": doi}
    db.flush()
    return d, v


def citing(db, owner, refs, **kw):
    d, v = seed(db, owner, title="Citing paper", year=2024, **kw)
    v.references = refs
    db.flush()
    return d, v


def targets(db, v):
    return [
        c.target_document_id
        for c in db.scalars(
            select(DocCitation).where(DocCitation.src_version_id == v.id).order_by(DocCitation.ref_no)
        )
    ]


@pytest.mark.parametrize("ref_doi", ["10.1000/ABC.1", "https://doi.org/10.1000/abc.1", "doi:10.1000/abc.1"])
def test_doi_match_case_and_prefix_insensitive(db_session, ana, ref_doi):
    t, _ = lib(db_session, ana, "Target", doi="DOI:10.1000/ABC.1")
    _, v = citing(db_session, ana, [{"title": "unrelated", "year": 1999, "doi": ref_doi}])
    assert resolve_references(db_session, v) == 1
    assert targets(db_session, v) == [t.id]
    assert db_session.get(DocCitation, (v.id, 1)).confidence == 1.0


def test_fuzzy_title_and_year_match(db_session, ana):
    t, _ = lib(db_session, ana, "Attention Is All You Need", year=2017)
    _, v = citing(db_session, ana, [{"title": "attention is all you need.", "year": 2017}])
    assert resolve_references(db_session, v) == 1
    row = db_session.get(DocCitation, (v.id, 1))
    assert row.target_document_id == t.id and row.confidence == 1.0  # case/punct-normalised: exact


def test_near_miss_title_not_matched(db_session, ana):
    lib(db_session, ana, "Attention Is All You Need", year=2017)
    _, v = citing(db_session, ana, [{"title": "Attention Is All You Need In Practice Today", "year": 2017}])
    assert resolve_references(db_session, v) == 0
    assert targets(db_session, v) == [None]


def test_same_title_different_year_not_matched(db_session, ana):
    lib(db_session, ana, "Attention Is All You Need", year=2017)
    _, v = citing(db_session, ana, [{"title": "Attention Is All You Need", "year": 2018}])
    assert resolve_references(db_session, v) == 0


def test_resolve_idempotent_ref_no_and_no_self_link(db_session, ana):
    _, v = citing(db_session, ana, [{"title": "x", "year": 1}, {"title": "Citing paper", "year": 2024}])
    assert resolve_references(db_session, v) == 0 and resolve_references(db_session, v) == 0
    rows = db_session.scalars(select(DocCitation).where(DocCitation.src_version_id == v.id)).all()
    assert sorted(r.ref_no for r in rows) == [1, 2] and all(r.target_document_id is None for r in rows)


def test_backfill_links_earlier_dangling_reference(db_session, ana):
    _, v = citing(db_session, ana, [{"title": "Late Arrival Paper", "year": 2019}])
    assert resolve_references(db_session, v) == 0
    t, _ = lib(db_session, ana, "Late Arrival Paper", year=2019)
    assert backfill_for(db_session, t) == 1
    assert targets(db_session, v) == [t.id]
    assert backfill_for(db_session, t) == 0


def test_handler_resolves_and_backfills(db_session, ana):
    t, _ = lib(db_session, ana, "Late Arrival Paper", year=2019)
    _, v = citing(db_session, ana, [{"title": "Late Arrival Paper", "year": 2019}])
    handle_citations(db_session, {"version_id": str(v.id)})
    assert targets(db_session, v) == [t.id]
    _, other = citing(db_session, ana, [{"title": "Newer Paper On Citations", "year": 2025}])
    handle_citations(db_session, {"version_id": str(other.id)})
    n, nv = lib(db_session, ana, "Newer Paper On Citations", year=2025)
    handle_citations(db_session, {"version_id": str(nv.id)})
    assert targets(db_session, other) == [n.id]
    handle_citations(db_session, {"version_id": "00000000-0000-0000-0000-000000000000"})  # unknown: no-op


def test_references_endpoint_hides_invisible_target(client, as_ana, as_bob, ana, bob, db_session):
    pub, _ = lib(db_session, ana, "Public Paper", year=2020, doi="10.1/pub")
    priv, _ = lib(db_session, ana, "Private Paper On Matters", year=2021, visibility="private")
    d, v = citing(
        db_session,
        ana,
        [
            {"title": "Public Paper", "authors": ["A B", "C D"], "year": 2020, "doi": "10.1/pub"},
            {"title": "Private Paper On Matters", "year": 2021},
            {"title": "Nowhere", "year": 1990},
        ],
    )
    resolve_references(db_session, v)
    mine = client.get(f"/documents/{d.id}/references", headers=as_ana).json()
    assert [r["ref_no"] for r in mine] == [1, 2, 3]
    assert mine[0]["raw"] == "A B, C D (2020). Public Paper. 10.1/pub"
    assert mine[0]["target"] == {"doc_id": str(pub.id), "title": "Public Paper"}
    assert (
        mine[1]["target"] == {"doc_id": str(priv.id), "title": "Private Paper On Matters"}
        and mine[2]["target"] is None
    )
    theirs = client.get(f"/documents/{d.id}/references", headers=as_bob).json()
    assert [r["target"] is not None for r in theirs] == [True, False, False]


def test_references_404_when_source_invisible(client, as_bob, ana, db_session):
    d, _ = citing(db_session, ana, [], visibility="private")
    for sub in ("references", "cited-by"):
        r = client.get(f"/documents/{d.id}/{sub}", headers=as_bob)
        assert (r.status_code, r.json()) == (404, {"detail": "not found"})


def test_cited_by_excludes_invisible_citing_docs(client, as_bob, ana, db_session):
    t, _ = lib(db_session, ana, "Target Work Of Interest", year=2020)
    ref = [{"title": "Target Work Of Interest", "year": 2020}]
    ok, okv = citing(db_session, ana, ref, venue="ICML")
    _, hv = citing(db_session, ana, ref, visibility="private")
    _, pv = citing(db_session, ana, ref, stage="review")
    for v in (okv, hv, pv):
        resolve_references(db_session, v)
    r = client.get(f"/documents/{t.id}/cited-by", headers=as_bob).json()
    assert r == [
        {"doc_id": str(ok.id), "title": "Citing paper", "authors": ["Ann"], "year": 2024, "venue": "ICML"}
    ]


def test_search_cited_by_count_visible_only(client, as_bob, ana, db_session):
    from api_seed import FakeES

    from roracle.api.routes.library import get_es

    t, _ = lib(db_session, ana, "Target Work Of Interest", year=2020)
    ref = [{"title": "Target Work Of Interest", "year": 2020}]
    for kw in ({}, {}, {"visibility": "private"}):
        _, v = citing(db_session, ana, ref, **kw)
        resolve_references(db_session, v)
    client.app.dependency_overrides[get_es] = lambda: FakeES()
    res = client.get("/search", headers=as_bob).json()["results"]
    by = {r["doc_id"]: r["cited_by_count"] for r in res}
    assert by[str(t.id)] == 2


def test_job_kind_registered(settings, session_factory):
    from roracle.worker import build_handlers

    assert "citations" in build_handlers(settings, session_factory)


def test_fuzzy_confidence_is_ratio(db_session, ana):
    t, _ = lib(db_session, ana, "Deep Residual Learning for Image Recognition", year=2016)
    _, v = citing(
        db_session, ana, [{"title": "Deep Residual Learning for Image Recognitions", "year": 2016.0}]
    )
    assert resolve_references(db_session, v) == 1
    row = db_session.get(DocCitation, (v.id, 1))
    assert row.target_document_id == t.id and 0.92 <= row.confidence < 1.0


def test_short_generic_title_never_fuzzy_matches(db_session, ana):
    lib(db_session, ana, "Introduction", year=2020)
    lib(db_session, ana, "Deep learning", year=2020)
    _, v = citing(
        db_session, ana, [{"title": "Introduction", "year": 2020}, {"title": "Deep learning", "year": 2020}]
    )
    assert resolve_references(db_session, v) == 0


def test_tied_candidates_not_linked(db_session, ana):
    lib(db_session, ana, "A Survey of Graph Neural Networks", year=2020)
    lib(db_session, ana, "A Survey of Graph Neural Networks", year=2020)
    _, v = citing(db_session, ana, [{"title": "A Survey of Graph Neural Networks", "year": 2020}])
    assert resolve_references(db_session, v) == 0


def test_doi_normalisation_and_oldest_wins(db_session, ana):
    from datetime import UTC, datetime, timedelta

    old, _ = lib(db_session, ana, "First", doi="10.5/x")
    new, _ = lib(db_session, ana, "Second", doi="https://doi.org/10.5/X")
    now = datetime.now(UTC)  # server_default now() ties inside one transaction: set explicitly
    old.created_at, new.created_at = now - timedelta(hours=1), now
    db_session.flush()
    _, v = citing(db_session, ana, [{"doi": "https://www.doi.org/10.5%2Fx)."}])
    assert resolve_references(db_session, v) == 1 and targets(db_session, v) == [old.id]


def test_non_owner_non_searchable_source_404(client, as_bob, ana, db_session):
    d, _ = citing(db_session, ana, [{"title": "x"}], stage="review")
    for sub in ("references", "cited-by"):
        r = client.get(f"/documents/{d.id}/{sub}", headers=as_bob)
        assert (r.status_code, r.json()) == (404, {"detail": "not found"})


def test_handler_noop_when_not_searchable_or_deleted(db_session, ana):
    from datetime import UTC, datetime

    _, v = citing(db_session, ana, [{"title": "Whatever Title Here Now", "year": 1}], stage="review")
    handle_citations(db_session, {"version_id": str(v.id)})
    d2, v2 = citing(db_session, ana, [{"title": "Whatever Title Here Now", "year": 1}])
    d2.deleted_at = datetime.now(UTC)
    db_session.flush()
    handle_citations(db_session, {"version_id": str(v2.id)})
    assert db_session.scalars(select(DocCitation)).all() == []


def test_mark_searchable_and_enqueue_is_atomic(db_session, session_factory, ana):
    from roracle.ingest.state import mark_searchable_and_enqueue
    from roracle.models import DocumentVersion, Job

    _, v = citing(db_session, ana, [], stage="embedded")
    db_session.commit()
    mark_searchable_and_enqueue(session_factory, v.id)
    db_session.expire_all()
    assert db_session.get(DocumentVersion, v.id).stage == "searchable"
    assert [j.payload for j in db_session.scalars(select(Job).where(Job.kind == "citations"))] == [
        {"version_id": str(v.id)}
    ]
    with pytest.raises(LookupError):
        mark_searchable_and_enqueue(session_factory, __import__("uuid").uuid4())
    assert db_session.scalars(select(Job).where(Job.kind == "citations")).all().__len__() == 1
