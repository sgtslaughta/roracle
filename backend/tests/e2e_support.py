"""Shared real-stack fixtures/helpers for the e2e tests (see test_e2e_ingest.py for the pattern)."""

import os
import time
from pathlib import Path

import pytest
from elasticsearch import Elasticsearch
from openai import OpenAI

from roracle.queue import run_once
from roracle.search_index import index_name
from roracle.worker import build_handlers

FIXTURES = Path(__file__).parent / "fixtures"
MAX_WAIT_S = 180  # brief says 60; first docling/LLM calls are slow
E2E_MARKS = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("ROR_E2E") != "1", reason="set ROR_E2E=1 to run"),
    pytest.mark.usefixtures("_blob_store_override"),
]


@pytest.fixture
def e2e_settings(settings, test_bucket):
    tunnel = {"llm_base_url": "http://localhost:21434/v1", "summary_model": "qwen2.5:14b"}
    overrides = {k: v for k, v in tunnel.items() if f"ROR_{k.upper()}" not in os.environ}
    return settings.model_copy(
        update={
            **overrides,
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


def upload_and_ingest(client, headers, session_factory, settings, es, name, data, ctype, **form):
    """POST /documents then drain the queue with real handlers. Returns (document_id, version_id, seconds)."""
    r = client.post(
        "/documents",
        headers=headers,
        data={"title": name, "visibility": "private", **form},
        files={"file": (name, data, ctype)},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    es.created_versions.append(body["version_id"])
    handlers = build_handlers(settings, session_factory)
    t0 = time.monotonic()
    while time.monotonic() - t0 < MAX_WAIT_S:
        if not run_once(session_factory, handlers):
            break
    elapsed = time.monotonic() - t0
    assert elapsed < MAX_WAIT_S, "ingest did not go idle in time"
    es.indices.refresh(index=index_name(settings.embed_model))
    return body["document_id"], body["version_id"], elapsed


def real_llm(settings) -> OpenAI:
    return OpenAI(
        base_url=settings.llm_base_url, api_key=settings.llm_api_key, timeout=settings.llm_timeout_s
    )
