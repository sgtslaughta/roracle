"""sync_signals against a real Elasticsearch: head chunk only, noop when unchanged."""

import uuid

import pytest
from api_seed import seed
from elasticsearch import Elasticsearch

from roracle.ingest.chunking import Chunk
from roracle.models import Vote
from roracle.search_index import acl_fields, ensure_index, index_chunks, index_name
from roracle.search_signals import sync_signals

pytestmark = pytest.mark.integration
DIMS = 4


def test_sync_signals_head_only_and_noop(settings, session_factory, db_session, ana):
    es = Elasticsearch(settings.elastic_url)
    model = f"Test Model:{uuid.uuid4().hex[:8]}"
    ensure_index(es, model, DIMS)
    try:
        d, v = seed(db_session, ana)
        db_session.add(Vote(user_id=ana, document_id=d.id, value=1))
        db_session.commit()
        chunks = [
            Chunk(n=i, text=f"t{i}", contextual=f"t{i}", headings=[], pages=[1], bboxes=[]) for i in range(2)
        ]
        acl = acl_fields(db_session, d)
        index_chunks(es, v, d, chunks, [[1.0, 0, 0, 0]] * 2, acl, model, title="T", refresh=True)
        assert sync_signals(es, session_factory, [str(d.id)]) == 1
        hits = {
            h["_source"]["chunk_id"]: h["_source"]
            for h in es.search(index=index_name(model), size=10)["hits"]["hits"]
        }
        head, tail = hits[f"{v.id}:0"], hits[f"{v.id}:1"]
        assert (head["votes_net"], head["cited_by_global"], head["searchable"]) == (1, 0, True)
        assert not {"votes_net", "cited_by_global", "searchable"} & tail.keys()
        assert sync_signals(es, session_factory, [str(d.id)]) == 0
    finally:
        es.indices.delete(index=index_name(model), ignore_unavailable=True)
        es.close()
