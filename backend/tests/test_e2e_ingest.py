"""End-to-end ingest on the real local stack (MinIO, Docling, ES, model endpoint). Run: ROR_E2E=1 pytest -m e2e."""

import os
import time
from pathlib import Path

import pytest
from elasticsearch import Elasticsearch
from openai import OpenAI
from sqlalchemy import select

from roracle.ingest.embed import embed, query_prompt
from roracle.models import DocumentVersion
from roracle.queue import run_once
from roracle.search_index import index_name
from roracle.worker import build_handlers

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("ROR_E2E") != "1", reason="set ROR_E2E=1 to run"),
    pytest.mark.usefixtures("_blob_store_override"),
]

FIXTURES = Path(__file__).parent / "fixtures"
MAX_WAIT_S = 180  # brief says 60; first docling/LLM calls are slow


@pytest.fixture
def e2e_settings(settings, test_bucket):
    env = os.environ.get
    return settings.model_copy(
        update={
            "llm_base_url": env("ROR_LLM_BASE_URL", "http://localhost:21434/v1"),
            "embed_model": env("ROR_EMBED_MODEL", "embeddinggemma"),
            "embed_dims": int(env("ROR_EMBED_DIMS", "768")),
            "summary_model": env("ROR_SUMMARY_MODEL", "qwen2.5:14b"),
            "docling_url": env("ROR_DOCLING_URL", "http://localhost:5001"),
            "s3_bucket": test_bucket,  # same bucket as the API store override
            "grobid_url": "",
            "clamav_host": "",
        }
    )


@pytest.fixture
def es(e2e_settings):
    """ES client; the index name is fixed per model, so remove only this test's chunks afterwards."""
    c = Elasticsearch(e2e_settings.elastic_url)
    c.created_versions = []
    yield c
    for vid in c.created_versions:
        c.delete_by_query(index="chunks", query={"term": {"version_id": vid}}, refresh=True)


@pytest.mark.parametrize(
    ("fixture", "ctype", "query"),
    [
        ("sample.md", "text/markdown", "quokka heuristic lexical surprise reranking"),
        ("sample.pdf", "application/pdf", "sample document"),
    ],
)
def test_ingest_end_to_end(
    client, as_ana, db_session, session_factory, e2e_settings, es, fixture, ctype, query, capsys
):
    r = client.post(
        "/documents",
        headers=as_ana,
        data={"title": f"E2E {fixture}", "visibility": "private"},
        files={"file": (fixture, (FIXTURES / fixture).read_bytes(), ctype)},
    )
    assert r.status_code == 201, r.text
    vid = r.json()["version_id"]
    es.created_versions.append(vid)

    handlers = build_handlers(e2e_settings, session_factory)
    t0 = time.monotonic()
    while time.monotonic() - t0 < MAX_WAIT_S:
        if not run_once(session_factory, handlers):
            break
    elapsed = time.monotonic() - t0
    assert elapsed < MAX_WAIT_S, "ingest did not go idle in time"

    db_session.expire_all()
    v = db_session.scalars(select(DocumentVersion).where(DocumentVersion.id == vid)).one()
    assert v.stage == "searchable", v.stage
    assert v.summary and v.summary.strip()

    es.indices.refresh(index=index_name(e2e_settings.embed_model))
    hits = es.search(index="chunks", query={"term": {"version_id": vid}}, size=100)["hits"]["hits"]
    assert len(hits) >= 1
    src = hits[0]["_source"]
    if fixture == "sample.pdf":
        assert src["pages"] and src["bboxes"], "PDF chunks must carry pages and bboxes"

    llm = OpenAI(base_url=e2e_settings.llm_base_url, api_key=e2e_settings.llm_api_key)
    [qvec] = embed(llm, e2e_settings.embed_model, [query_prompt(query)], e2e_settings.embed_dims)
    knn = es.search(
        index="chunks",
        knn={
            "field": "embedding",
            "query_vector": qvec,
            "k": 5,
            "num_candidates": 50,
            "filter": {"term": {"version_id": vid}},
        },
        size=5,
    )["hits"]["hits"]
    assert knn and knn[0]["_source"]["version_id"] == vid
    assert len(es.get(index=hits[0]["_index"], id=hits[0]["_id"])["_source"]["embedding"]) == 768

    mine = client.get("/me/uploads", headers=as_ana).json()["items"]
    assert [i["stage"] for i in mine if i["version_id"] == vid] == ["searchable"]
    with capsys.disabled():
        print(
            f"\n[e2e] {fixture}: {elapsed:.1f}s chunks={len(hits)} pages={src['pages']} "
            f"knn_top_score={knn[0]['_score']:.4f} summary={v.summary[:60]!r}"
        )
