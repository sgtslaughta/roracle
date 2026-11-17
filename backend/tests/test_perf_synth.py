import subprocess
import sys
import uuid

import pytest
from elasticsearch import Elasticsearch
from sqlalchemy import func, select, text

from roracle.models import Document, DocumentCoauthor, DocumentShare, Job, Tag, User
from roracle.perf import synth_es, synth_pg


def _seed(url, **kw):
    return synth_pg.seed(url, docs=2000, users=50, jobs_done=300, jobs_queued=90, audit_rows=40, **kw)


def test_seed_pg_row_counts_and_determinism(db_session, settings):
    counts = _seed(settings.database_url)
    assert counts["documents"] == counts["versions"] == 2000
    assert counts["tags"] == 4000 and counts["citations"] == 10_000
    assert counts["shares"] == 2 * 200 and counts["coauthors"] == 100
    db = db_session
    assert db.scalar(select(func.count()).select_from(Document)) == 2000
    assert db.scalar(select(func.count()).select_from(User)) == 50
    assert db.scalar(select(func.count()).select_from(DocumentShare)) == 400
    assert db.scalar(select(func.count()).select_from(DocumentCoauthor)) == 100
    assert db.scalar(select(func.count()).select_from(Tag)) == 4000
    assert db.scalar(select(func.count()).select_from(Job).where(Job.status == "queued")) == 90
    assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "rebuild_units")) == 30
    assert db.get(Document, synth_pg.det_uuid(1, "d", 7)) is not None
    ids = set(db.scalars(select(Document.id)))
    assert uuid.UUID(int=0) not in ids
    db.rollback()
    db.execute(text("TRUNCATE users, documents, jobs, audit_log, blobs RESTART IDENTITY CASCADE"))
    db.commit()
    _seed(settings.database_url)  # same seed, same ids
    assert set(db.scalars(select(Document.id))) == ids


@pytest.mark.integration
def test_seed_es_count_mapping_and_guard(settings):
    es = Elasticsearch(settings.elastic_url)
    index = f"perf-test-{uuid.uuid4().hex[:8]}"
    try:
        n = synth_es.seed(es, index, chunks=1000, dims=8, docs=100, personas={"users": 10}, batch=250)
        assert n == 1000
        props = es.indices.get_mapping(index=index)[index]["mappings"]["properties"]
        assert props["embedding"]["dims"] == 8
        doc = es.get(index=index, id=f"{synth_pg.det_uuid(1, 'v', 0)}:0")["_source"]
        assert doc["doc_id"] == str(synth_pg.det_uuid(1, "d", 0)) and doc["visibility"] == "shared"
        vis = es.search(index=index, size=0, aggs={"v": {"terms": {"field": "visibility"}}})
        got = {b["key"]: b["doc_count"] for b in vis["aggregations"]["v"]["buckets"]}
        assert got == {"everyone": 600, "org_unit": 150, "private": 150, "shared": 100}
        with pytest.raises(ValueError):
            synth_es.seed(es, "chunks-x", chunks=1, dims=8, docs=1, personas={})
    finally:
        es.indices.delete(index=index, ignore_unavailable=True)


def test_alias_override_via_env():
    code = "from roracle import search_index as s; print(s.ALIAS, s.MUTATE_ALIAS)"
    env = {"ROR_READ_ALIAS": "perf-chunks", "ROR_MUTATE_ALIAS": "perf-chunks-all"}
    import os

    out = subprocess.run(
        [sys.executable, "-c", code], env={**os.environ, **env}, check=False, capture_output=True, text=True
    )
    assert out.stdout.split() == ["perf-chunks", "perf-chunks-all"], out.stderr
    out = subprocess.run(
        [sys.executable, "-c", code], env=os.environ, check=False, capture_output=True, text=True
    )
    assert out.stdout.split() == ["chunks", "chunks-all"]
