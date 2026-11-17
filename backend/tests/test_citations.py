import pytest
from api_seed import seed
from sqlalchemy import select

from roracle.citations import backfill_for, handle_citations, resolve_references
from roracle.models import DocCitation, DocumentVersion


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
    assert (
        db_session.scalars(select(DocCitation).where(DocCitation.src_version_id == v.id)).one().confidence
        == 1.0
    )


def test_fuzzy_title_and_year_match(db_session, ana):
    t, _ = lib(db_session, ana, "Attention Is All You Need", year=2017)
    _, v = citing(db_session, ana, [{"title": "attention is all you need.", "year": 2017}])
    assert resolve_references(db_session, v) == 1
    row = db_session.scalars(select(DocCitation).where(DocCitation.src_version_id == v.id)).one()
    assert row.target_document_id == t.id and row.confidence == 1.0  # case/punct-normalised: exact


def test_near_miss_title_not_matched(db_session, ana):
    lib(db_session, ana, "Attention Is All You Need", year=2017)
    _, v = citing(db_session, ana, [{"title": "Attention Is All You Need In Practice Today", "year": 2017}])
    assert resolve_references(db_session, v) == 0
    assert targets(db_session, v) == []


def test_same_title_different_year_not_matched(db_session, ana):
    lib(db_session, ana, "Attention Is All You Need", year=2017)
    _, v = citing(db_session, ana, [{"title": "Attention Is All You Need", "year": 2018}])
    assert resolve_references(db_session, v) == 0


def test_resolve_idempotent_ref_no_and_no_self_link(db_session, ana):
    _, v = citing(db_session, ana, [{"title": "x", "year": 1}, {"title": "Citing paper", "year": 2024}])
    assert resolve_references(db_session, v) == 0 and resolve_references(db_session, v) == 0
    rows = db_session.scalars(select(DocCitation).where(DocCitation.src_version_id == v.id)).all()
    assert rows == []  # unmatched references have no rows


def test_backfill_links_earlier_dangling_reference(db_session, ana):
    _, v = citing(db_session, ana, [{"title": "Late Arrival Paper", "year": 2019}])
    assert resolve_references(db_session, v) == 0
    t, _ = lib(db_session, ana, "Late Arrival Paper", year=2019)
    assert backfill_for(db_session, t) == 1
    assert targets(db_session, v) == [t.id]
    assert backfill_for(db_session, t) == 1 and targets(db_session, v) == [t.id]  # rebuild is idempotent


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
    row = db_session.scalars(select(DocCitation).where(DocCitation.src_version_id == v.id)).one()
    assert row.target_document_id == t.id and 0.92 <= row.confidence < 1.0


def test_short_generic_title_never_fuzzy_matches(db_session, ana):
    lib(db_session, ana, "Introduction", year=2020)
    lib(db_session, ana, "Deep learning", year=2020)
    _, v = citing(
        db_session, ana, [{"title": "Introduction", "year": 2020}, {"title": "Deep learning", "year": 2020}]
    )
    assert resolve_references(db_session, v) == 0


def _aged(db, *docs):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)  # server_default now() ties inside one transaction: set explicitly
    for i, d in enumerate(docs):
        d.created_at = now - timedelta(hours=len(docs) - i)
    db.flush()


def test_all_candidates_stored_on_tie(db_session, ana):
    lib(db_session, ana, "A Survey of Graph Neural Networks", year=2020)
    lib(db_session, ana, "A Survey of Graph Neural Networks", year=2020)
    _, v = citing(db_session, ana, [{"title": "A Survey of Graph Neural Networks", "year": 2020}])
    assert resolve_references(db_session, v) == 1 and len(targets(db_session, v)) == 2


def test_doi_normalisation(db_session, ana):
    t, _ = lib(db_session, ana, "First", doi="10.5/x")
    _, v = citing(db_session, ana, [{"doi": "https://www.doi.org/10.5%2Fx)."}])
    assert resolve_references(db_session, v) == 1 and targets(db_session, v) == [t.id]


def refs(client, hdr, d):
    return client.get(f"/documents/{d.id}/references", headers=hdr).json()


def test_private_older_doi_clash_does_not_hijack_or_leak(client, as_ana, as_bob, ana, bob, db_session):
    priv, _ = lib(db_session, ana, "Hidden", doi="10.5/x", visibility="private")
    pub, _ = lib(db_session, ana, "Visible", doi="10.5/x")
    _aged(db_session, priv, pub)  # private is the oldest
    d, v = citing(db_session, ana, [{"doi": "10.5/x"}])
    resolve_references(db_session, v)
    assert refs(client, as_bob, d)[0]["target"] == {"doc_id": str(pub.id), "title": "Visible"}
    assert refs(client, as_ana, d)[0]["target"]["doc_id"] == str(priv.id)  # sees both: oldest wins
    assert client.get(f"/documents/{pub.id}/cited-by", headers=as_bob).json()[0]["doc_id"] == str(d.id)


def test_only_invisible_candidate_is_unresolved(client, as_bob, ana, bob, db_session):
    lib(db_session, ana, "Hidden", doi="10.5/y", visibility="private")
    d, v = citing(db_session, ana, [{"doi": "10.5/y"}])
    resolve_references(db_session, v)
    assert refs(client, as_bob, d)[0]["target"] is None


def test_fuzzy_tie_visible_vs_invisible_links_visible(client, as_bob, ana, bob, db_session):
    priv, _ = lib(db_session, ana, "A Survey of Graph Neural Networks", year=2020, visibility="private")
    pub, _ = lib(db_session, ana, "A Survey of Graph Neural Networks", year=2020)
    _aged(db_session, priv, pub)
    d, v = citing(db_session, ana, [{"title": "A Survey of Graph Neural Networks", "year": 2020}])
    resolve_references(db_session, v)
    assert refs(client, as_bob, d)[0]["target"]["doc_id"] == str(pub.id)


def test_best_confidence_beats_age(client, as_ana, ana, db_session):
    old, _ = lib(db_session, ana, "A Survey of Graph Neural Network", year=2020)  # fuzzy < 1.0, older
    new, _ = lib(db_session, ana, "Unrelated", year=1, doi="10.5/z")  # DOI 1.0, newer
    _aged(db_session, old, new)
    d, v = citing(
        db_session, ana, [{"title": "A Survey of Graph Neural Networks", "year": 2020, "doi": "10.5/z"}]
    )
    resolve_references(db_session, v)
    assert refs(client, as_ana, d)[0]["target"]["doc_id"] == str(new.id)


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
    from roracle.models import Job

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


def test_reference_target_title_falls_back(client, as_ana, ana, db_session):
    _, va = lib(db_session, ana, None, doi="10.1/a")
    va.meta = {**va.meta, "title": "Meta Title"}
    lib(db_session, ana, None, doi="10.1/b", filename="b-paper.pdf")
    d, v = citing(db_session, ana, [{"doi": "10.1/a"}, {"doi": "10.1/b"}])
    resolve_references(db_session, v)
    got = client.get(f"/documents/{d.id}/references", headers=as_ana).json()
    assert [r["target"]["title"] for r in got] == ["Meta Title", "b-paper.pdf"]


def test_cited_by_limit_param(client, as_bob, ana, db_session):
    t, _ = lib(db_session, ana, "Target Work Of Interest", year=2020)
    ref = [{"title": "Target Work Of Interest", "year": 2020}]
    for _ in range(2):
        _, v = citing(db_session, ana, ref)
        resolve_references(db_session, v)
    url = f"/documents/{t.id}/cited-by"
    assert len(client.get(url, headers=as_bob).json()) == 2
    assert len(client.get(url + "?limit=1", headers=as_bob).json()) == 1
    assert client.get(url + "?limit=0", headers=as_bob).status_code == 422


def test_cited_by_pages_count_documents_not_version_rows(client, as_bob, ana, db_session):
    t, _ = lib(db_session, ana, "Target Work Of Interest", year=2020)
    ref = [{"title": "Target Work Of Interest", "year": 2020}]
    d1, _ = citing(db_session, ana, ref, versions=2)
    for v in db_session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == d1.id)):
        v.is_latest, v.references = True, ref  # corrupted: both versions flagged latest
        db_session.flush()
        resolve_references(db_session, v)
    d2, v2 = citing(db_session, ana, ref)
    resolve_references(db_session, v2)
    url = f"/documents/{t.id}/cited-by"
    pages = [client.get(f"{url}?limit=1&offset={o}", headers=as_bob).json() for o in (0, 1, 2)]
    assert [len(x) for x in pages] == [1, 1, 0]
    assert {pages[0][0]["doc_id"], pages[1][0]["doc_id"]} == {str(d1.id), str(d2.id)}


def _old_counts(db, p, ids):
    """The pre-10.6 query: window over the whole table, target filter applied afterwards."""
    from sqlalchemy import func

    from roracle.access import visible_documents
    from roracle.citations import best_targets
    from roracle.models import Document

    bt = best_targets(p)
    rows = db.execute(
        select(bt.c.target_document_id, func.count(func.distinct(Document.id)))
        .join(DocumentVersion, DocumentVersion.id == bt.c.src_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            bt.c.rn == 1,
            bt.c.target_document_id.in_(ids),
            DocumentVersion.is_latest.is_(True),
            DocumentVersion.stage == "searchable",
            visible_documents(p),
        )
        .group_by(bt.c.target_document_id)
    ).all()
    return {str(t): n for t, n in rows}


def test_scoped_counts_and_best_targets_equal_the_unscoped_query(db_session, ana, bob):
    import random

    from roracle.auth import Principal
    from roracle.citations import best_targets, cited_by_counts

    rnd = random.Random(7)
    docs = [
        lib(db_session, rnd.choice([ana, bob]), f"T{i}", visibility=rnd.choice(["everyone", "private"]))[0]
        for i in range(12)
    ]
    srcs = [
        citing(db_session, rnd.choice([ana, bob]), [], visibility=rnd.choice(["everyone", "private"]))[1]
        for _ in range(10)
    ]
    for v in srcs:
        for ref_no in range(1, 4):  # several candidates per reference: ties and rank order matter
            for t in rnd.sample(docs, rnd.randint(1, 4)):
                db_session.add(
                    DocCitation(
                        src_version_id=v.id,
                        ref_no=ref_no,
                        target_document_id=t.id,
                        confidence=rnd.choice([0.8, 0.9, 1.0]),
                    )
                )
    db_session.flush()
    ids = [d.id for d in docs]
    for who in (ana, bob):
        p = Principal(user_id=who, sub="s", username="u", groups=(), is_admin=False)
        assert cited_by_counts(db_session, p, ids) == _old_counts(db_session, p, ids) != {}
        assert cited_by_counts(db_session, p, ids[:3]) == _old_counts(db_session, p, ids[:3])
        full = best_targets(p)
        for sid in {v.id for v in srcs[:3]}:
            scoped = best_targets(p, sources=[sid])
            a = db_session.execute(
                select(scoped.c.ref_no, scoped.c.target_document_id).where(scoped.c.rn == 1)
            ).all()
            b = db_session.execute(
                select(full.c.ref_no, full.c.target_document_id).where(
                    full.c.src_version_id == sid, full.c.rn == 1
                )
            ).all()
            assert sorted(a) == sorted(b)
        sc = best_targets(p, targets=ids[:4])  # a kept group's winner may be a non-target
        winners = {
            (s, r): t
            for s, r, t in db_session.execute(
                select(sc.c.src_version_id, sc.c.ref_no, sc.c.target_document_id).where(sc.c.rn == 1)
            )
        }
        ref = {
            (s, r): t
            for s, r, t in db_session.execute(
                select(full.c.src_version_id, full.c.ref_no, full.c.target_document_id).where(full.c.rn == 1)
            )
        }
        assert all(ref[k] == t for k, t in winners.items())
