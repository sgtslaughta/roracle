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
