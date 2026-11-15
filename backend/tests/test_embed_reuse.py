"""Exact-duplicate chunk embedding reuse, scoped to one document (no cross-document reuse)."""

import json
import uuid

import pytest
from elasticsearch import Elasticsearch
from ingest_helpers import FakeLLM, handler, make_version
from sqlalchemy import update

from roracle.ingest import pipeline
from roracle.models import Document, DocumentVersion
from roracle.queue import enqueue, run_once
from roracle.search_index import cached_vectors, index_name

pytestmark = pytest.mark.integration
DIMS = 4


class CountingLLM(FakeLLM):
    def __init__(self, dims):
        super().__init__(dims)
        self.inputs: list[str] = []

    def _embed(self, model, input):
        self.inputs.extend(input)
        return super()._embed(model, input)

    def chunk_inputs(self):
        return [t for t in self.inputs if "Paragraph" in t]


@pytest.fixture
def cfg(settings):
    return settings.model_copy(
        update={"embed_model": f"fake-embed-{uuid.uuid4().hex[:8]}", "embed_dims": DIMS, "grobid_url": ""}
    )


@pytest.fixture
def es(cfg):
    c = Elasticsearch(cfg.elastic_url)
    yield c
    c.indices.delete(index=index_name(cfg.embed_model), ignore_unavailable=True)


def det_doc(texts: list[str]) -> dict:
    from docling_core.types.doc import DocItemLabel, DoclingDocument
    from docling_core.types.doc.base import Size

    d = DoclingDocument(name="t")
    d.add_page(page_no=1, size=Size(width=612, height=792))
    parent = d.add_text(label=DocItemLabel.TITLE, text="A Study")
    for t in texts:
        d.add_text(parent=parent, label=DocItemLabel.TEXT, text=t)
    return d.export_to_dict()


def paras(n, changed=None):
    return [
        f"Paragraph {i} about retrieval " * 60 + (f" CHANGED {changed}" if i == 0 and changed else "")
        for i in range(n)
    ]


def ingest(db_session, session_factory, cfg, store, es, v, texts, summary="Sum A."):
    store.put_bytes(pipeline.docling_key(v.id), json.dumps(det_doc(texts)).encode(), "application/json")
    db_session.execute(
        update(DocumentVersion).where(DocumentVersion.id == v.id).values(stage="deduped", summary=summary)
    )
    db_session.commit()
    llm = CountingLLM(DIMS)
    with session_factory() as s:
        enqueue(s, "ingest", {"version_id": str(v.id)})
        s.commit()
    assert run_once(session_factory, {"ingest": handler(cfg, store, es, llm, session_factory)})
    db_session.expire_all()
    assert db_session.get(DocumentVersion, v.id).stage == "searchable"
    return llm


def test_rerun_reuses_all(db_session, session_factory, cfg, store, es, user):
    v = make_version(db_session, store, user, title="T")
    texts = paras(3)
    first = ingest(db_session, session_factory, cfg, store, es, v, texts)
    assert first.chunk_inputs()
    again = ingest(db_session, session_factory, cfg, store, es, v, texts)
    assert again.chunk_inputs() == []


def test_new_version_embeds_only_changed(db_session, session_factory, cfg, store, es, user):
    v1 = make_version(db_session, store, user, title="T")
    n1 = len(ingest(db_session, session_factory, cfg, store, es, v1, paras(3)).chunk_inputs())
    v2 = make_version(db_session, store, user, doc=db_session.get(Document, v1.document_id), version=2)
    llm = ingest(db_session, session_factory, cfg, store, es, v2, paras(3, changed="x"))
    assert n1 >= 2
    assert len(llm.chunk_inputs()) == 1


def test_new_version_other_summary_embeds_all(db_session, session_factory, cfg, store, es, user):
    v1 = make_version(db_session, store, user, title="T")
    n1 = len(ingest(db_session, session_factory, cfg, store, es, v1, paras(3)).chunk_inputs())
    v2 = make_version(db_session, store, user, doc=db_session.get(Document, v1.document_id), version=2)
    llm = ingest(db_session, session_factory, cfg, store, es, v2, paras(3), summary="Sum B.")
    assert len(llm.chunk_inputs()) == n1


def test_summary_reaches_chunk_prompt(db_session, session_factory, cfg, store, es, user):
    v = make_version(db_session, store, user, title="T")
    llm = ingest(db_session, session_factory, cfg, store, es, v, paras(2), summary="Sum A.")
    inputs = llm.chunk_inputs()
    assert inputs
    assert all(t.startswith("title: T | text: Sum A.\n\n") for t in inputs)


def test_other_document_embeds_all(db_session, session_factory, cfg, store, es, user):
    texts = paras(3)
    v1 = make_version(db_session, store, user, title="T")
    n1 = len(ingest(db_session, session_factory, cfg, store, es, v1, texts).chunk_inputs())
    v2 = make_version(db_session, store, user, title="T")
    assert len(ingest(db_session, session_factory, cfg, store, es, v2, texts).chunk_inputs()) == n1


def test_wrong_dims_cached_is_miss(db_session, session_factory, cfg, store, es, user):
    v = make_version(db_session, store, user, title="T")
    ingest(db_session, session_factory, cfg, store, es, v, paras(2))
    es.indices.refresh(index=index_name(cfg.embed_model))
    hit = es.search(index="chunks", query={"term": {"doc_id": str(v.document_id)}}, size=1)["hits"]["hits"][0]
    sha = hit["_source"]["prompt_sha"]
    assert len(cached_vectors(es, cfg.embed_model, v.document_id, [sha], DIMS)) == 1
    assert cached_vectors(es, cfg.embed_model, v.document_id, [sha], DIMS + 1) == {}
    assert cached_vectors(es, cfg.embed_model, uuid.uuid4(), [sha], DIMS) == {}
    assert cached_vectors(es, "no-such-model-xyz", v.document_id, [sha], DIMS) == {}
