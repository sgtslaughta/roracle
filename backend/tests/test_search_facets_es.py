"""find_aggs against real ES: literal, case-insensitive matching, never a 500."""

import uuid

import pytest
from elasticsearch import Elasticsearch

from roracle.search_facets import facet_aggs, find_aggs
from roracle.search_params import SearchParams

pytestmark = pytest.mark.integration
AUTHORS = ["O'Brien (ed.)", "Smith", "Ñúñez"]


@pytest.mark.parametrize(
    "find,want",
    [
        ("smi", ["Smith"]),
        ("ñú", ["Ñúñez"]),
        ("(ed.)", ["O'Brien (ed.)"]),
        (".*", []),
        ("SMITH", ["Smith"]),
        ("o'b", ["O'Brien (ed.)"]),
    ],
)
def test_find_aggs_literal(settings, find, want):
    es = Elasticsearch(settings.elastic_url)
    idx = f"roracle-facets-{uuid.uuid4().hex[:8]}"
    try:
        es.indices.create(index=idx, mappings={"properties": {"authors": {"type": "keyword"}}})
        for i, a in enumerate(AUTHORS):
            es.index(index=idx, id=str(i), document={"authors": [a]})
        es.indices.refresh(index=idx)
        agg = find_aggs("author", find, SearchParams())
        r = es.search(index=idx, size=0, aggs={"f": agg})
        got = sorted(b["key"] for b in r["aggregations"]["f"]["v"]["buckets"])
        assert got == want
    finally:
        es.indices.delete(index=idx, ignore_unavailable=True)
        es.close()


def test_facet_aggs_without_collections_accepted(settings):
    es = Elasticsearch(settings.elastic_url)
    idx = f"roracle-facets-{uuid.uuid4().hex[:8]}"
    try:
        es.indices.create(index=idx, mappings={"properties": {"year": {"type": "integer"}}})
        r = es.search(index=idx, size=0, aggs=facet_aggs(SearchParams(), {}))
        assert "f_year" in r["aggregations"]
    finally:
        es.indices.delete(index=idx, ignore_unavailable=True)
        es.close()
