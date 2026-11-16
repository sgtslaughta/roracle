"""Re-embed against real ES + Ollama (embeddinggemma): same model, generation 2, search keeps working."""

import uuid

import pytest
from elasticsearch import Elasticsearch
from sqlalchemy import select

from roracle import clients, reembed, runtime, search_index
from roracle.auth import Principal
from roracle.ingest.chunking import Chunk
from roracle.ingest.embed import doc_prompt, embed
from roracle.labels.policy import Policy
from roracle.models import Document, DocumentVersion, Job
from roracle.retrieval import search_chunks
from roracle.search_index import acl_fields, ensure_index, index_chunks, index_name

pytestmark = pytest.mark.integration
MODEL, DIMS = "embeddinggemma:latest", 768  # distinct slug: never collides with the default index


@pytest.fixture
def cfg(settings):
    return settings.model_copy(
        update={
            "embed_model": MODEL,
            "embed_dims": DIMS,
            "llm_base_url": "http://localhost:21434/v1",
            "rerank_url": "",
            "read_alias": f"chunks-test-{uuid.uuid4().hex[:8]}",  # never touch the shared `chunks` alias
        }
    )


def _holders(c):
    return set(c.indices.get_alias(name="chunks")) if c.indices.exists_alias(name="chunks") else set()


@pytest.fixture
def es(cfg):
    c = Elasticsearch(cfg.elastic_url)
    shared = _holders(c)
    yield c
    assert _holders(c) == shared  # the shared dev-ES read alias was never touched (R2-m2)
    c.indices.delete(index=[index_name(MODEL, 1), index_name(MODEL, 2)], ignore_unavailable=True)
    c.close()


def test_reindex_same_model_to_gen_2(es, cfg, db_session, session_factory, user, blob, monkeypatch):
    llm = clients.llm(cfg)
    ensure_index(es, MODEL, DIMS, cfg.read_alias)
    d = Document(owner_id=user.id, title="Quokka", visibility="everyone", org_unit="/")
    db_session.add(d)
    db_session.flush()
    v = DocumentVersion(
        id=uuid.uuid4(), document_id=d.id, version=1, blob_sha256=blob.sha256, filename="a.pdf",
        content_type="x", size=1, is_latest=True, meta={},
    )  # fmt: skip
    db_session.add(v)
    db_session.flush()
    texts = ["the quokka is a small marsupial", "a car is an automobile with four wheels"]
    chunks = [Chunk(i, t, t, [], [1], [{"page": 1}]) for i, t in enumerate(texts)]
    vecs = embed(llm, MODEL, [doc_prompt("Quokka", t) for t in texts], DIMS)
    index_chunks(es, v, d, chunks, vecs, acl_fields(db_session, d), MODEL, title="Quokka")
    db_session.commit()
    p = Principal(user_id=uuid.uuid4(), sub="s", username="u", groups=(), is_admin=False)

    def search(settings):
        return search_chunks(es, llm, settings, p, "marsupial quokka", policy=Policy([]), filters={})

    assert search(cfg)
    reembed.start(db_session, es, cfg, MODEL, DIMS)
    db_session.commit()
    for _ in range(20):
        j = db_session.scalar(
            select(Job).where(Job.kind == "reembed", Job.status == "queued").order_by(Job.id)
        )
        if j is None:
            break
        j.status = "done"
        reembed.handle_reembed(
            db_session, j.payload, es=es, llm=llm, session_factory=session_factory, settings=cfg
        )
        db_session.commit()
        es.indices.refresh(index=index_name(MODEL, 2))
    gen2 = index_name(MODEL, 2)
    assert es.count(index=gen2)["count"] == 2
    assert set(es.indices.get_alias(name=cfg.read_alias)) == {gen2}
    eff = runtime.effective(db_session, cfg)
    assert search_index.active_index(eff) == gen2
    hits = search(eff)
    assert hits and hits[0].chunk_id.endswith(":0")
    search_index.sync_acl(
        es, d.id, lambda: ({**acl_fields(db_session, d), "visibility": "private"}, "Quokka")
    )
    for gen in (1, 2):  # mutation alias reaches both generations
        es.indices.refresh(index=index_name(MODEL, gen))
        assert es.get(index=index_name(MODEL, gen), id=f"{v.id}:0")["_source"]["visibility"] == "private"
    own, all_old = index_name(MODEL, 1)[:-1], reembed._old
    # chunks-all is shared and hardcoded: only ever see (and delete) this model's generations
    monkeypatch.setattr(reembed, "_old", lambda *a: [i for i in all_old(*a) if i.startswith(own)])
    assert reembed.drop_old(db_session, es, cfg) == [index_name(MODEL, 1)]
