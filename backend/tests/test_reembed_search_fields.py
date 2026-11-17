"""Backfill of the library-search fields through the re-embed generation path (real ES 9.x, fake embedder)."""

import uuid

import pytest
from elasticsearch import Elasticsearch
from reembed_support import FakeLlm, drain
from sqlalchemy import select

from roracle import reembed
from roracle.ingest.chunking import Chunk
from roracle.ingest.embed import chunk_prompt, prompt_sha
from roracle.models import Document, DocumentVersion
from roracle.search_index import acl_fields, ensure_index, index_chunks, index_name

pytestmark = pytest.mark.integration
OLD, OTHER, DIMS = "libsearch-old:1", "libsearch-other:1", 8
TEXTS = ["the quokka is a small marsupial", "a car is an automobile"]


@pytest.fixture
def cfg(settings):
    return settings.model_copy(
        update={
            "embed_model": OLD,
            "embed_dims": DIMS,
            "reembed_slices": 1,
            "read_alias": f"chunks-test-{uuid.uuid4().hex[:8]}",  # never touch the shared `chunks` alias
        }
    )


@pytest.fixture
def es(cfg):
    c = Elasticsearch(cfg.elastic_url)
    yield c
    c.indices.delete(index=[index_name(m, g) for m in (OLD, OTHER) for g in (1, 2)], ignore_unavailable=True)
    c.close()


@pytest.fixture
def old_index(es, cfg, db_session, user, blob):
    """A pre-change generation: no `_meta.search_fields`, chunks without any search field."""
    ensure_index(es, OLD, DIMS, cfg.read_alias)
    d = Document(owner_id=user.id, title="Quokka", visibility="everyone", org_unit="/")
    db_session.add(d)
    db_session.flush()
    v = DocumentVersion(
        id=uuid.uuid4(), document_id=d.id, version=1, blob_sha256=blob.sha256, filename="a.pdf",
        content_type="x", size=1, is_latest=True, stage="searchable", page_count=3,
        meta={"venue": "Nature", "abstract": "marsupials", "authors": ["A. Smith"]},
    )  # fmt: skip
    db_session.add(v)
    db_session.flush()
    chunks = [Chunk(i, t, t, [], [1], [{"page": 1}]) for i, t in enumerate(TEXTS)]
    vecs = [[0.5] * DIMS, [0.25] * DIMS]
    shas = [prompt_sha(chunk_prompt("Quokka", t, None, None)) for t in TEXTS]  # what ingest stamped
    index_chunks(es, v, d, chunks, vecs, acl_fields(db_session, d), OLD, title="Quokka", prompt_shas=shas)
    db_session.commit()
    name = index_name(OLD, 1)
    es.update_by_query(  # strip what a pre-change ingest never wrote
        index=name, refresh=True,
        script={"source": "for (f in params.f) { ctx._source.remove(f) }", "params": {"f": [
            "venue", "venue_sort", "has_doi", "issn", "ids_present", "has_abstract", "has_refs", "page_count", "added_at",
            "title_sort", "first_author_sort", "authors_text", "abstract", "doc_head", "votes_net",
            "cited_by_global", "searchable"]}},
    )  # fmt: skip
    es.indices.put_mapping(index=name, meta={})
    assert not es.indices.get_mapping(index=name)[name]["mappings"].get("_meta")
    assert "venue" not in es.get(index=name, id=f"{v.id}:0")["_source"]
    return v


def _reindex(es, cfg, db_session, sf, model):
    llm = FakeLlm(DIMS)
    reembed.start(db_session, es, cfg, model, DIMS)
    db_session.commit()
    drain(db_session, sf, es, llm, cfg)
    gen2 = index_name(model, 2 if model == OLD else 1)  # a new model starts at generation 1
    es.indices.refresh(index=gen2)
    return llm, gen2


def test_same_model_backfills_fields_without_embedding(es, cfg, db_session, session_factory, old_index):
    llm, gen2 = _reindex(es, cfg, db_session, session_factory, OLD)
    assert es.indices.get_mapping(index=gen2)[gen2]["mappings"]["_meta"]["search_fields"] == 1
    head = es.get(index=gen2, id=f"{old_index.id}:0")["_source"]
    assert (head["venue"], head["doc_head"], head["searchable"], head["abstract"]) == (
        "Nature", True, True, "marsupials",
    )  # fmt: skip
    assert head["page_count"] == 3 and head["votes_net"] == 0 and head["cited_by_global"] == 0
    body = es.get(index=gen2, id=f"{old_index.id}:1")["_source"]
    assert body["venue"] == "Nature" and "doc_head" not in body and "searchable" not in body
    vec = es.search(index=gen2, query={"ids": {"values": [f"{old_index.id}:1"]}},
                    source={"includes": ["embedding"], "exclude_vectors": False})["hits"]["hits"][0]["_source"]  # fmt: skip
    assert vec["embedding"] == pytest.approx([0.25] * DIMS)  # the stored vector was copied
    assert llm.models == []  # no embedding call at all


def test_different_model_embeds_every_chunk(es, cfg, db_session, session_factory, old_index):
    llm, gen2 = _reindex(es, cfg, db_session, session_factory, OTHER)
    assert es.count(index=gen2)["count"] == len(TEXTS)
    assert llm.models and set(llm.models) == {OTHER}
    head = es.get(index=gen2, id=f"{old_index.id}:0")["_source"]
    assert head["venue"] == "Nature" and head["doc_head"] is True
    assert db_session.scalar(select(DocumentVersion.id)) is not None


def test_same_model_reembeds_stale_prompts(es, cfg, db_session, session_factory, old_index):
    """A summary written after ingest changes every chunk prompt: the stored vectors (and the stored summary
    vector) are stale, so the same-model copy embeds them instead of re-stamping them fresh."""
    name = index_name(OLD, 1)
    es.update(index=name, id=f"{old_index.id}:0", doc={"summary_embedding": [0.75] * DIMS}, refresh=True)
    old_index.summary = "Sum B."
    db_session.commit()
    llm, gen2 = _reindex(es, cfg, db_session, session_factory, OLD)
    assert llm.models  # embedded, not copied
    src = {
        h["_id"]: h["_source"]
        for h in es.search(index=gen2, query={"match_all": {}},
                           source={"includes": ["embedding", "summary_embedding", "prompt_sha"],
                                   "exclude_vectors": False})["hits"]["hits"]
    }  # fmt: skip
    assert src[f"{old_index.id}:1"]["embedding"] == pytest.approx([1.0] * DIMS)
    assert src[f"{old_index.id}:0"]["summary_embedding"] == pytest.approx([1.0] * DIMS)
    want = prompt_sha(chunk_prompt("Quokka", TEXTS[1], "Sum B.", None))
    assert src[f"{old_index.id}:1"]["prompt_sha"] == want
