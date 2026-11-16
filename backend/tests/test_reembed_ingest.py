"""Ingest vs. re-embed cutover (C1/I3): the handler uses the persisted active model/index, never the env default."""

import json

import pytest
from ingest_helpers import FakeLLM, handler, make_version
from sqlalchemy import update
from test_embed_reuse import det_doc, paras

from roracle import runtime, search_index
from roracle.ingest import pipeline
from roracle.models import DocumentVersion
from roracle.queue import enqueue, run_once

pytestmark = pytest.mark.integration


class ModelLLM(FakeLLM):
    def __init__(self, dims):
        super().__init__(dims)
        self.models: list[str] = []

    def _embed(self, model, input):
        self.models.append(model)
        return super()._embed(model, input)


@pytest.fixture
def new_index(cfg, es):
    name = f"{search_index.index_name(cfg.embed_model)}-next"
    yield name
    es.indices.delete(index=name, ignore_unavailable=True)


def _stage(db_session, store, v, **vals):
    store.put_bytes(pipeline.docling_key(v.id), json.dumps(det_doc(paras(2))).encode(), "application/json")
    db_session.execute(
        update(DocumentVersion).where(DocumentVersion.id == v.id).values(stage="deduped", **vals)
    )
    db_session.commit()


def _run(session_factory, cfg, store, es, llm, v):
    with session_factory() as s:
        enqueue(s, "ingest", {"version_id": str(v.id)})
        s.commit()
    run_once(session_factory, {"ingest": handler(cfg, store, es, llm, session_factory)})


def test_ingest_after_cutover_uses_new_model_and_index(
    db_session, session_factory, cfg, store, es, user, new_index
):
    runtime.set_active_embedding(db_session, "cut-model", 4, new_index)  # what a cutover persists
    db_session.commit()
    v = make_version(db_session, store, user, title="T")
    _stage(db_session, store, v, summary="Sum A.")
    llm = ModelLLM(4)
    _run(session_factory, cfg, store, es, llm, v)
    db_session.expire_all()
    assert db_session.get(DocumentVersion, v.id).stage == "searchable"
    es.indices.refresh(index=new_index)
    hits = es.search(index=new_index, query={"term": {"doc_id": str(v.document_id)}})["hits"]["hits"]
    assert hits and {h["_source"]["embedding_model"] for h in hits} == {"cut-model"}
    assert set(llm.models) == {"cut-model"}  # chunk AND summary embeddings
    assert not es.indices.exists(
        index=search_index.index_name(cfg.embed_model)
    )  # old-default index untouched


def test_summary_backfill_does_not_write_old_model_vector(
    db_session, session_factory, cfg, store, es, user, new_index, monkeypatch
):
    v = make_version(db_session, store, user, title="T")
    _stage(db_session, store, v, summary="Sum A.", summary_embedding=None)
    real = search_index.cached_vectors

    def cutover_midway(*a, **kw):  # cutover commits after the handler read its settings
        with session_factory() as s:
            runtime.set_active_embedding(s, "cut-model", 4, new_index)
            s.commit()
        return real(*a, **kw)

    monkeypatch.setattr(search_index, "cached_vectors", cutover_midway)
    _run(session_factory, cfg, store, es, ModelLLM(4), v)
    db_session.expire_all()
    got = db_session.get(DocumentVersion, v.id)
    assert got.summary_embedding is None and got.stage != "searchable"  # guard fired before the backfill
