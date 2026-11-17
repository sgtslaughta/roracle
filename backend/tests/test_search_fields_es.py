"""Search-field marker and additive mapping upgrade against a real Elasticsearch."""

import uuid

import pytest
from elasticsearch import Elasticsearch

from roracle import search_fields
from roracle.search_index import _mapping, ensure_index, index_name

pytestmark = pytest.mark.integration
DIMS = 4


@pytest.fixture
def es(settings):
    c = Elasticsearch(settings.elastic_url)
    yield c
    c.close()


@pytest.fixture
def cfg(settings, es):
    model = f"Test Model:{uuid.uuid4().hex[:8]}"
    search_fields._READY.clear()
    yield settings.model_copy(update={"embed_model": model, "embed_dims": DIMS, "embed_index": None})
    es.indices.delete(index=index_name(model), ignore_unavailable=True)
    search_fields._READY.clear()


def test_new_index_has_marker(es, cfg):
    name = ensure_index(es, cfg.embed_model, DIMS)
    assert es.indices.get_mapping(index=name)[name]["mappings"]["_meta"] == {"search_fields": 1}
    assert search_fields.fields_ready(es, cfg) is True


def test_old_index_not_ready_and_gets_fields(es, cfg):
    name = index_name(cfg.embed_model)
    old = _mapping(DIMS)
    old.pop("_meta")
    for f in search_fields.SEARCH_PROPS:
        old["properties"].pop(f)
    es.indices.create(index=name, mappings=old)
    assert search_fields.fields_ready(es, cfg) is False
    ensure_index(es, cfg.embed_model, DIMS)
    props = es.indices.get_mapping(index=name)[name]["mappings"]["properties"]
    assert "venue" in props and props["abstract"]["type"] == "text"
    search_fields._READY.clear()
    assert search_fields.fields_ready(es, cfg) is False  # marker needs a new generation (reindex)


def test_missing_index_ready(es, cfg):
    assert search_fields.fields_ready(es, cfg) is True
