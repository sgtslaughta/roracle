"""Live TEI reranker check (integration). Skips unless ROR_RERANK_URL answers /health.
Dev: ROR_RERANK_URL=http://localhost:21480/rerank ROR_RERANK_API=tei ROR_RERANK_MODEL=bge-reranker-v2-m3"""

import math
import os
import time
from urllib.parse import urlsplit

import httpx
import pytest

from roracle.config import Settings
from roracle.retrieval import Passage, _rerank

URL = os.environ.get("ROR_RERANK_URL", "")
BUDGET_S = 0.8  # p95 at depth 200 (plan Task 4)
RUNS = 50


def _up() -> bool:
    if not URL:
        return False
    u = urlsplit(URL)
    try:
        return httpx.get(f"{u.scheme}://{u.netloc}/health", timeout=3).is_success
    except httpx.HTTPError:
        return False


pytestmark = [pytest.mark.integration, pytest.mark.skipif(not _up(), reason="no reranker at ROR_RERANK_URL")]


def _settings() -> Settings:
    return Settings(
        database_url="x", s3_endpoint="x", s3_access_key="x", s3_secret_key="x", oidc_issuer="x",
        oidc_jwks_url="x", rerank_url=URL, rerank_api=os.environ.get("ROR_RERANK_API", "tei"),
        rerank_model=os.environ.get("ROR_RERANK_MODEL", ""), rerank_api_key=os.environ.get("ROR_RERANK_API_KEY", ""),
        rerank_depth=200, rerank_timeout_s=10.0,
    )  # fmt: skip


def _passage(i: int, text: str) -> Passage:
    return Passage(f"c{i}", f"d{i}", "v", text, "", [1], [], 1 - i / 1000, title="T")


def test_relevant_first():
    ps = [
        _passage(0, "The recipe calls for two cups of flour and a pinch of salt."),
        _passage(1, "Mitochondria are the organelles that produce most of the cell's ATP."),
        _passage(2, "The stock market closed higher on Tuesday after earnings reports."),
    ]
    out = _rerank(_settings(), "Which organelle produces ATP in cells?", ps, None)
    assert out[0].chunk_id == "c1"


def test_p95_within_budget_at_depth_200():
    # ~1500-char passages; the page-length distractors differ so batching cannot be cached away.
    filler = "Retrieval pipelines combine lexical and dense signals before a cross-encoder reorders them. "
    ps = [_passage(i, f"Passage {i}. " + filler * 16) for i in range(200)]
    s, times = _settings(), []
    for _ in range(RUNS):
        t0 = time.perf_counter()
        out = _rerank(s, "How do retrieval pipelines combine signals?", ps, None)
        times.append(time.perf_counter() - t0)
        assert len(out) == 200
    times.sort()
    p95 = times[math.ceil(0.95 * RUNS) - 1]
    print(f"rerank depth=200 runs={RUNS} p50={times[RUNS // 2] * 1000:.0f}ms p95={p95 * 1000:.0f}ms")
    assert p95 <= BUDGET_S
