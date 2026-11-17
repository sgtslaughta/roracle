import uuid

import pytest
from elasticsearch import Elasticsearch
from sqlalchemy import select

from roracle.ingest.chunking import Chunk
from roracle.models import DocumentShare, DocumentVersion
from roracle.models_labels import Label
from roracle.search_index import (
    acl_fields,
    cached_vectors,
    delete_stale_chunks,
    ensure_index,
    index_chunks,
    index_name,
    mark_previous_versions,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def es(settings):
    return Elasticsearch(settings.elastic_url)


@pytest.fixture
def model(es):
    m = f"Test Model:{uuid.uuid4().hex[:8]}"
    yield m
    es.indices.delete(index=index_name(m), ignore_unavailable=True)


def test_index_name_slug():
    assert index_name("EmbeddingGemma:300m_v2") == "chunks-embeddinggemma-300m-v2-1"


def test_ensure_index_idempotent_and_mapping(es, model):
    n1 = ensure_index(es, model, 4)
    n2 = ensure_index(es, model, 4)
    assert n1 == n2 == index_name(model)
    assert "chunks" in es.indices.get_alias(index=n1)[n1]["aliases"]
    props = es.indices.get_mapping(index=n1)[n1]["mappings"]["properties"]
    assert props["embedding"]["dims"] == 4 and props["embedding"]["similarity"] == "cosine"
    assert props["embedding"]["index_options"]["type"] == "int8_hnsw"
    assert props["text"]["analyzer"] == "english" and props["title"]["analyzer"] == "english"
    for f in ("owner_id", "visibility", "org_unit", "share_users", "share_groups"):
        assert props[f]["type"] == "keyword"
    assert props["bboxes"]["enabled"] is False


def test_acl_fields(db_session, user, document):
    db_session.add_all(
        [
            DocumentShare(document_id=document.id, principal_kind="user", principal="u1"),
            DocumentShare(document_id=document.id, principal_kind="group", principal="/Physics"),
        ]
    )
    db_session.flush()
    assert acl_fields(db_session, document) == {
        "owner_id": str(user.id),
        "visibility": "private",
        "org_unit": "/",
        "coauthor_users": [],
        "share_users": ["u1"],
        "share_groups": ["/Physics"],
        "label_id": str(db_session.scalar(select(Label.id).where(Label.is_default))),
    }


def _v(document, blob, n=1):
    return DocumentVersion(
        id=uuid.uuid4(), document_id=document.id, version=n, blob_sha256=blob.sha256, filename="a",
        content_type="x", size=1, is_latest=True,
    )  # fmt: skip


def _chunks(n):
    return [Chunk(i, f"text {i}", f"ctx {i}", ["H"], [1], [{"page": 1}]) for i in range(n)]


def test_index_overwrite_stale_and_latest_flip(es, model, db_session, document, blob):
    ensure_index(es, model, 4)
    v1, v2 = _v(document, blob), _v(document, blob, 2)
    acl = acl_fields(db_session, document)
    vec = [[0.1, 0.2, 0.3, 0.4]] * 3
    assert index_chunks(es, v1, document, _chunks(3), vec, acl, model, title="T") == 3
    index_chunks(es, v1, document, _chunks(3), vec, acl, model, title="T")  # re-run: same _ids
    delete_stale_chunks(es, v1.id, 2)  # re-run shrank to 2 chunks
    count = lambda q: es.count(index=index_name(model), query=q)["count"]
    assert count({"term": {"version_id": str(v1.id)}}) == 2
    index_chunks(es, v2, document, _chunks(1), vec[:1], acl, model, title="T")
    mark_previous_versions(es, document.id, v2.version)
    es.indices.refresh(index=index_name(model))
    assert (
        count({"bool": {"filter": [{"term": {"version_id": str(v1.id)}}, {"term": {"is_latest": False}}]}})
        == 2
    )
    assert (
        count({"bool": {"filter": [{"term": {"version_id": str(v2.id)}}, {"term": {"is_latest": True}}]}})
        == 1
    )


def test_bulk_error_raises(es, model, db_session, document, blob):
    ensure_index(es, model, 4)
    with pytest.raises(RuntimeError):
        index_chunks(es, _v(document, blob), document, _chunks(1), [[0.1] * 3], {}, model, title="T")


def test_ensure_index_dims_mismatch(es, model):
    from roracle.queue import NonRetryable

    ensure_index(es, model, 4)
    with pytest.raises(NonRetryable, match="dimension mismatch"):
        ensure_index(es, model, 8)


def test_file_type():
    from roracle.search_index import file_type

    assert [file_type(n) for n in ("a.PDF", "b.docx", "c.md", "noext", "x.tar.gz", "", None, "a.b c")] == [
        "pdf", "docx", "md", "other", "gz", "other", "other", "other",
    ]  # fmt: skip


def test_ensure_index_adds_missing_fields_to_old_index(es, model):
    name = index_name(model)
    es.indices.create(
        index=name,
        mappings={"properties": {"embedding": {"type": "dense_vector", "dims": 4, "index": True,
                                               "similarity": "cosine"}}},
    )  # fmt: skip
    ensure_index(es, model, 4)
    ensure_index(es, model, 4)
    props = es.indices.get_mapping(index=name)[name]["mappings"]["properties"]
    for f in ("tags", "file_type", "collections", "doc_id", "authors"):
        assert props[f]["type"] == "keyword"


def test_index_chunks_file_type_and_tags(es, model, db_session, document, blob):
    ensure_index(es, model, 4)
    v = _v(document, blob)
    v.filename = "Paper.PDF"
    index_chunks(es, v, document, _chunks(1), [[0.1] * 4], {}, model, title="T", tags=["ml"])
    es.indices.refresh(index=index_name(model))
    src = es.search(index=index_name(model))["hits"]["hits"][0]["_source"]
    assert src["file_type"] == "pdf" and src["tags"] == ["ml"]


def test_summary_vector_only_on_chunk_zero(es, model, db_session, document, blob):
    ensure_index(es, model, 4)
    index_chunks(
        es, _v(document, blob), document, _chunks(2), [[0.1] * 4] * 2, {}, model, title="T",
        summary_vector=[0.5] * 4,
    )  # fmt: skip
    es.indices.refresh(index=index_name(model))
    got = es.search(index=index_name(model), fields=["summary_embedding"])["hits"]["hits"]  # not in `_source`
    srcs = {h["_source"]["chunk_id"].split(":")[1]: h.get("fields", {}) for h in got}
    assert srcs["0"]["summary_embedding"] == [0.5] * 4 and "summary_embedding" not in srcs["1"]


def test_ensure_index_backfills_summary_embedding_and_checks_dims(es, model):
    from roracle.queue import NonRetryable
    from roracle.search_index import _mapping

    name = index_name(model)
    props = {k: v for k, v in _mapping(4)["properties"].items() if k != "summary_embedding"}
    es.indices.create(index=name, mappings={"properties": props})
    ensure_index(es, model, 4)
    got = es.indices.get_mapping(index=name)[name]["mappings"]["properties"]["summary_embedding"]
    assert got["type"] == "dense_vector" and got["dims"] == 4
    with pytest.raises(NonRetryable, match="dimension mismatch"):
        ensure_index(es, model, 8)


def test_ensure_index_dims_mismatch_does_not_mutate_mapping(es, model):
    from roracle.queue import NonRetryable
    from roracle.search_index import _mapping

    name = index_name(model)
    props = {k: v for k, v in _mapping(4)["properties"].items() if k != "summary_embedding"}
    es.indices.create(index=name, mappings={"properties": props})
    with pytest.raises(NonRetryable, match="dimension mismatch"):
        ensure_index(es, model, 8)
    assert "summary_embedding" not in es.indices.get_mapping(index=name)[name]["mappings"]["properties"]
    ensure_index(es, model, 4)


def test_index_is_created_with_explicit_options_and_settings(es, settings, model):
    s = settings.model_copy(update={"es_shards": 2, "es_replicas": 0, "es_refresh_interval": "7s"})
    name = ensure_index(es, model, 128, settings=s)
    got = es.indices.get_settings(index=name)[name]["settings"]["index"]
    assert (got["number_of_shards"], got["number_of_replicas"], got["refresh_interval"]) == ("2", "0", "7s")
    assert got["dense_vector"]["hnsw_filter_heuristic"] == "acorn"  # ACORN filtered kNN
    assert got["mapping"]["exclude_source_vectors"] == "true"  # vectors out of _source
    props = es.indices.get_mapping(index=name)[name]["mappings"]["properties"]
    for f in ("embedding", "summary_embedding"):
        assert props[f]["index_options"]["type"] == "int8_hnsw"  # explicit, never the server default


def test_bbq_option_adds_rescore_oversample_and_filtered_knn_works(es, settings, model):
    s = settings.model_copy(
        update={"vector_index_type": "bbq_hnsw", "vector_rescore_oversample": 4.0, "es_replicas": 0}
    )
    name = ensure_index(es, model, 128, settings=s)
    opts = es.indices.get_mapping(index=name)[name]["mappings"]["properties"]["embedding"]["index_options"]
    assert opts["type"] == "bbq_hnsw" and opts["rescore_vector"] == {"oversample": 4.0}
    es.index(index=name, id="a", document={"doc_id": "d1", "embedding": [0.3] * 128}, refresh=True)
    r = es.search(
        index=name,
        knn={
            "field": "embedding", "query_vector": [0.3] * 128, "k": 1, "num_candidates": 10,
            "filter": {"term": {"doc_id": "d1"}},
        },
    )  # fmt: skip
    assert r["hits"]["total"]["value"] == 1


def test_vectors_are_not_in_source_but_cached_vectors_and_updates_keep_them(es, settings, model):
    name = ensure_index(es, model, 4, settings=settings.model_copy(update={"es_replicas": 0}))
    es.index(
        index=name, id="a", refresh=True,
        document={"doc_id": "d1", "prompt_sha": "p1", "embedding": [0.25, 0.5, 0.75, 1.0]},
    )  # fmt: skip
    assert "embedding" not in es.get(index=name, id="a")["_source"]
    assert cached_vectors(es, model, "d1", ["p1"], 4) == {"p1": [0.25, 0.5, 0.75, 1.0]}
    es.update_by_query(
        index=name, query={"term": {"doc_id": "d1"}},
        script={"source": "ctx._source.title = 'x'", "lang": "painless"}, refresh=True,
    )  # fmt: skip
    assert cached_vectors(es, model, "d1", ["p1"], 4) == {
        "p1": [0.25, 0.5, 0.75, 1.0]
    }  # ACL-style rewrite keeps them
