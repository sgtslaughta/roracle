import uuid

import pytest
from conftest import bearer
from elasticsearch import Elasticsearch
from ingest_helpers import FakeLLM, docling_doc, fake_convert, handler
from sqlalchemy import select

from roracle.ingest import pipeline
from roracle.models import Document, DocumentVersion, Job
from roracle.queue import run_once
from roracle.search_index import index_name

pytestmark = pytest.mark.usefixtures("_blob_store_override")


def _up(client, headers, data=None, **form):
    data = data or uuid.uuid4().bytes
    r = client.post(
        "/documents", headers=headers, data=form, files={"file": ("p.pdf", data, "application/pdf")}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _title_url(b):
    return f"/documents/{b['document_id']}/versions/{b['version_id']}/title"


def _stage(db_session, vid, stage):
    db_session.execute(
        DocumentVersion.__table__.update().where(DocumentVersion.id == vid).values(stage=stage)
    )
    db_session.commit()


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


def test_upload_without_title_keeps_null(client, as_ana, db_session):
    _up(client, as_ana)
    assert db_session.scalar(select(Document.title)) is None


def test_put_title_rules(client, as_ana, as_bob, admin, db_session):
    b = _up(client, as_ana, visibility="private")
    assert client.put(_title_url(b), headers=as_ana, json={"title": "T"}).status_code == 409  # wrong stage
    _stage(db_session, b["version_id"], "needs_title")
    assert client.put(_title_url(b), headers=as_bob, json={"title": "T"}).status_code == 404  # not visible
    assert client.put(_title_url(b), headers=as_ana, json={"title": ""}).status_code == 422
    assert client.put(_title_url(b), headers=as_ana, json={"title": "x" * 501}).status_code == 422
    r = client.put(_title_url(b), headers=as_ana, json={"title": " Hello "})
    assert r.status_code == 200 and r.json()["stage"] == "parsed"
    db_session.expire_all()
    assert db_session.scalar(select(Document.title)) == "Hello"
    assert db_session.scalars(select(Job).order_by(Job.id.desc())).first().payload == {
        "version_id": b["version_id"]
    }


def test_put_title_visible_non_owner_forbidden_admin_allowed(client, as_ana, as_bob, admin, db_session):
    b = _up(client, as_ana, visibility="everyone")
    _stage(db_session, b["version_id"], "needs_title")
    assert client.put(_title_url(b), headers=as_bob, json={"title": "T"}).status_code == 403
    assert client.put(_title_url(b), headers=admin, json={"title": "T"}).status_code == 200


def test_admin_needs_title_listing(client, as_ana, admin, db_session):
    b = _up(client, as_ana)
    _up(client, as_ana)  # stays uploaded: not listed
    _stage(db_session, b["version_id"], "needs_title")
    assert client.get("/admin/needs-title", headers=as_ana).status_code == 403
    r = client.get("/admin/needs-title", headers=admin)
    assert r.status_code == 200
    (item,) = r.json()
    assert item["document_id"] == b["document_id"] and item["version_id"] == b["version_id"]
    assert item["filename"] == "p.pdf" and item["owner"] == "ana" and item["uploaded_at"]


@pytest.mark.integration
def test_title_then_resume_to_searchable(
    client, as_ana, db_session, session_factory, settings, store, monkeypatch
):
    cfg = settings.model_copy(update={"embed_model": f"fake-{uuid.uuid4().hex[:8]}", "embed_dims": 4})
    es = Elasticsearch(cfg.elastic_url)
    monkeypatch.setattr(pipeline, "convert", fake_convert(docling_doc(title=None)))
    b = _up(client, as_ana)
    run = lambda: run_once(session_factory, {"ingest": handler(cfg, store, es, FakeLLM(4), session_factory)})
    try:
        assert run()
        db_session.expire_all()
        assert db_session.get(DocumentVersion, uuid.UUID(b["version_id"])).stage == "needs_title"
        assert client.put(_title_url(b), headers=as_ana, json={"title": "Given Title"}).status_code == 200
        assert run()
        db_session.expire_all()
        v = db_session.get(DocumentVersion, uuid.UUID(b["version_id"]))
        assert v.stage == "searchable"
        es.indices.refresh(index=index_name(cfg.embed_model))
        hit = es.search(index="chunks", query={"term": {"version_id": b["version_id"]}})["hits"]["hits"][0]
        assert hit["_source"]["title"] == "Given Title"
    finally:
        es.indices.delete(index=index_name(cfg.embed_model), ignore_unavailable=True)
