"""Task 10.10 (H4): one ingest = ONE refresh, and the version is marked searchable only after it."""

import pytest
from elasticsearch import Elasticsearch
from ingest_helpers import docling_doc, fake_convert, make_version
from sqlalchemy import select
from test_ingest_handler import _run

from roracle.ingest import pipeline
from roracle.models import DocumentVersion
from roracle.search_index import index_name

pytestmark = pytest.mark.integration


LOG: list[
    tuple[str, str, str | None]
] = []  # (method, path, refresh query param); shared by `.options()` clones


class RecordingEs(Elasticsearch):
    """Real client that records every request."""

    def perform_request(self, method, path, *, params=None, **kw):
        LOG.append((method, path, (params or {}).get("refresh")))
        return super().perform_request(method, path, params=params, **kw)


def refreshes(es):
    explicit = [x for x in LOG if x[1].endswith("/_refresh")]
    implicit = [x for x in LOG if x[2] in ("true", "wait_for")]
    return explicit, implicit


@pytest.fixture
def rec(cfg):
    LOG.clear()
    return RecordingEs(cfg.elastic_url)


def test_one_ingest_issues_exactly_one_refresh_before_searchable(
    db_session, session_factory, cfg, store, rec, user, monkeypatch
):
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    stage_when_refreshed = []
    real = rec.indices.refresh

    def refresh(*a, **k):
        db_session.expire_all()
        stage_when_refreshed.append(db_session.scalars(select(DocumentVersion.stage)).first())
        return real(*a, **k)

    rec.indices.refresh = refresh
    v = make_version(db_session, store, user)
    _run(db_session, session_factory, cfg, store, rec, v)
    explicit, implicit = refreshes(rec)
    assert len(explicit) == 1, explicit  # the final one
    assert implicit == [], (
        implicit
    )  # bulk / delete / update_by_query force no refresh on a plain first ingest
    assert (
        stage_when_refreshed and "searchable" not in stage_when_refreshed
    )  # refreshed BEFORE the stage flips
    db_session.expire_all()
    assert db_session.get(DocumentVersion, v.id).stage == "searchable"
    # first index of the version: no stale-chunk delete_by_query and (version 1) no is_latest demotion
    assert not [x for x in LOG if "_delete_by_query" in x[1] or "_update_by_query" in x[1]]


def test_second_version_demotes_the_first_with_one_refresh(
    db_session, session_factory, cfg, store, rec, user, monkeypatch
):
    from roracle.models import Document

    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    v1 = make_version(db_session, store, user)
    _run(db_session, session_factory, cfg, store, rec, v1)
    doc = db_session.get(Document, v1.document_id)
    v2 = make_version(db_session, store, user, doc=doc, version=2)
    LOG.clear()
    _run(db_session, session_factory, cfg, store, rec, v2)
    explicit, implicit = refreshes(rec)
    assert len(explicit) == 1 and implicit == []
    assert (
        len([x for x in LOG if "_update_by_query" in x[1]]) == 1
    )  # mark_previous_versions, no refresh param


def test_ensure_index_metadata_calls_are_memoized(cfg, session_factory):
    from roracle.search_index import ensure_index

    es = RecordingEs(cfg.elastic_url)
    LOG.clear()
    try:
        ensure_index(es, cfg.embed_model, cfg.embed_dims, session_factory=session_factory, settings=cfg)
        n = len(LOG)
        ensure_index(es, cfg.embed_model, cfg.embed_dims, session_factory=session_factory, settings=cfg)
        assert len(LOG) == n and n > 0  # the second call made no ES request
    finally:
        es.indices.delete(index=index_name(cfg.embed_model), ignore_unavailable=True)


# ---- the ACL / label / DELETE race: a change committed right after our ACL re-read must still reach the new chunks


def _window(monkeypatch, es, sf, change, then):
    """Run `change` (a committed Postgres change) and `then` (what the API route does next: sync or purge) right
    AFTER pipeline's own read_acl returned: the narrowest window in which the new chunks could be missed."""
    real = pipeline.read_acl
    fired = []

    def read_acl(session_factory, doc_id):
        out = real(session_factory, doc_id)
        if not fired:
            fired.append(1)
            with sf() as s:
                change(s, doc_id)
                s.commit()
            then(es, sf, doc_id)
        return out

    monkeypatch.setattr(pipeline, "read_acl", read_acl)
    return fired


def _sync(es, sf, doc_id):
    from roracle.acl_sync import read_acl as fresh

    search_index_sync = __import__("roracle.search_index", fromlist=["sync_acl"]).sync_acl
    search_index_sync(es, doc_id, lambda: fresh(sf, doc_id))


def _purge(es, sf, doc_id):
    es.delete_by_query(
        index="chunks-all", query={"term": {"doc_id": str(doc_id)}}, refresh=True, conflicts="proceed"
    )


def _docs(es, cfg, v):
    es.indices.refresh(index=index_name(cfg.embed_model))
    return es.search(index=index_name(cfg.embed_model), query={"term": {"version_id": str(v.id)}}, size=100)[
        "hits"
    ]["hits"]


def _shared(db_session, store, user):
    from roracle.models import DocumentShare

    v = make_version(db_session, store, user, visibility="shared")
    db_session.add(DocumentShare(document_id=v.document_id, principal_kind="group", principal="/Physics"))
    db_session.commit()
    return v


def test_share_revoked_right_after_the_acl_read_still_reaches_new_chunks(
    db_session, session_factory, cfg, store, es, user, monkeypatch
):
    from roracle.models import DocumentShare

    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    v = _shared(db_session, store, user)
    fired = _window(
        monkeypatch, es, session_factory,
        lambda s, d: s.execute(DocumentShare.__table__.delete().where(DocumentShare.document_id == d)), _sync,
    )  # fmt: skip
    _run(db_session, session_factory, cfg, store, es, v)
    assert fired
    got = _docs(es, cfg, v)
    assert got and all(h["_source"]["share_groups"] == [] for h in got)  # the revocation reached EVERY chunk


def test_label_raised_right_after_the_acl_read_reaches_new_chunks(
    db_session, session_factory, cfg, store, es, user, monkeypatch
):
    from roracle.models import Document, Label

    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    v = make_version(db_session, store, user)
    lab = Label(name="Secret", colour="#111111", rank=9, exclude_llm=True)
    db_session.add(lab)
    db_session.commit()
    fired = _window(
        monkeypatch, es, session_factory,
        lambda s, d: s.get(Document, d).__setattr__("label_id", lab.id), _sync,
    )  # fmt: skip
    _run(db_session, session_factory, cfg, store, es, v)
    assert fired
    got = _docs(es, cfg, v)
    assert got and {h["_source"]["label_id"] for h in got} == {str(lab.id)}


def test_document_deleted_right_after_the_acl_read_leaves_no_chunks(
    db_session, session_factory, cfg, store, es, user, monkeypatch
):
    from datetime import UTC, datetime

    from roracle.models import Document

    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    v = make_version(db_session, store, user)
    fired = _window(
        monkeypatch, es, session_factory,
        lambda s, d: s.get(Document, d).__setattr__("deleted_at", datetime.now(UTC)), _purge,
    )  # fmt: skip
    _run(db_session, session_factory, cfg, store, es, v)
    assert fired and _docs(es, cfg, v) == []


def test_units_share_revocation_in_the_window_reaches_raptor_units(
    db_session, session_factory, cfg, store, es, user, monkeypatch
):
    from roracle.ingest.raptor import Unit
    from roracle.models import DocumentShare

    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc()))
    monkeypatch.setattr(
        pipeline, "build_units",
        lambda session, llm, settings, store, v, d, chunks: [
            Unit("section", "s0", "a section summary", ["H"], [f"{v.id}:0"], [1]),
            Unit("document", "d", "a document summary", [], [f"{v.id}:0"], [1]),
        ],
    )  # fmt: skip
    cfg = cfg.model_copy(update={"raptor": True})
    v = _shared(db_session, store, user)
    fired = _window(
        monkeypatch, es, session_factory,
        lambda s, d: s.execute(DocumentShare.__table__.delete().where(DocumentShare.document_id == d)), _sync,
    )  # fmt: skip
    _run(db_session, session_factory, cfg, store, es, v)
    assert fired
    got = _docs(es, cfg, v)
    units = [h for h in got if h["_source"].get("unit")]
    assert len(units) == 2 and all(h["_source"]["share_groups"] == [] for h in got)


def test_shadow_ensure_is_never_memoized_and_a_dropped_shadow_is_not_recreated(cfg, session_factory):
    from roracle.queue import NonRetryable
    from roracle.search_index import ensure_index

    es = RecordingEs(cfg.elastic_url)
    name = index_name(cfg.embed_model)
    try:
        ensure_index(es, cfg.embed_model, cfg.embed_dims, session_factory=session_factory, settings=cfg)
        ensure_index(
            es,
            cfg.embed_model,
            cfg.embed_dims,
            name=name,
            create=False,
            session_factory=session_factory,
            settings=cfg,
        )
        es.indices.delete(index=name)  # drop_shadow while the rebuild is running
        with pytest.raises(NonRetryable):
            ensure_index(
                es,
                cfg.embed_model,
                cfg.embed_dims,
                name=name,
                create=False,
                session_factory=session_factory,
                settings=cfg,
            )
    finally:
        es.indices.delete(index=name, ignore_unavailable=True)


def test_memo_is_shared_by_options_clones_of_one_connection(cfg, session_factory):
    from roracle.search_index import ensure_index

    es = RecordingEs(cfg.elastic_url)
    LOG.clear()
    try:
        ensure_index(es, cfg.embed_model, cfg.embed_dims, session_factory=session_factory, settings=cfg)
        n = len(LOG)
        ensure_index(
            es.options(request_timeout=5),
            cfg.embed_model,
            cfg.embed_dims,
            session_factory=session_factory,
            settings=cfg,
        )
        assert len(LOG) == n
    finally:
        es.indices.delete(index=index_name(cfg.embed_model), ignore_unavailable=True)
