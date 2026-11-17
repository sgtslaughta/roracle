"""OpenAI-spec embeddings (EmbeddingGemma prompt format)."""

import hashlib
import random
import threading
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait

import numpy as np

from roracle.queue import NonRetryable


class EmbedError(RuntimeError):
    pass


def doc_prompt(title: str, text: str, summary: str | None = None) -> str:
    if summary and summary.strip():
        return f"title: {title or 'none'} | text: {summary}\n\n{text}"
    return f"title: {title or 'none'} | text: {text}"


EMBED_PROMPT_VERSION = "2"  # hashed into prompt_sha: a bump re-embeds every chunk once (R8)


def chunk_prompt(title: str, contextual: str, summary: str | None, context: str | None) -> str:
    """A context note supersedes the summary prefix; without one the prompt is the plain `doc_prompt`."""
    if context and context.strip():
        return doc_prompt(title, f"{context}\n\n{contextual}")
    return doc_prompt(title, contextual, summary)


def prompt_sha(prompt: str) -> str:
    return hashlib.sha256(f"v{EMBED_PROMPT_VERSION}\n{prompt}".encode()).hexdigest()


def query_prompt(q: str) -> str:
    return f"task: search result | query: {q}"


RETRIES = 4  # attempts per request after the first
_BACKOFF_S, _BACKOFF_MAX_S = 0.5, 20.0
_RETRY_STATUS = {408, 429, 500, 502, 503, 504}
_pause = {"until": 0.0}  # shared throttle: after a 429 / Retry-After EVERY thread of this process holds off
_pause_lock = threading.Lock()
_sleep = time.sleep  # tests replace it


def _retry_after(e: Exception) -> float | None:
    try:
        return float(e.response.headers["retry-after"])  # type: ignore[attr-defined]
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _transient(e: Exception) -> bool:
    return getattr(e, "status_code", None) in _RETRY_STATUS or type(e).__name__ in (
        "APIConnectionError",
        "APITimeoutError",
    )


def _create(client, model: str, part: list[str]):
    """One embeddings request; 429/5xx/connection errors retried with jittered exponential backoff (Retry-After
    honoured, and shared with the other threads so a throttled endpoint is not hammered by the rest)."""
    for attempt in range(RETRIES + 1):
        if (wait_s := _pause["until"] - time.monotonic()) > 0:
            _sleep(wait_s)
        try:
            return client.embeddings.create(model=model, input=part)
        except Exception as e:
            if attempt == RETRIES or not _transient(e):
                raise
            delay = _retry_after(e) or min(_BACKOFF_MAX_S, _BACKOFF_S * 2**attempt)
            delay *= 1 + random.random() * 0.25
            with _pause_lock:
                _pause["until"] = max(_pause["until"], time.monotonic() + delay)


def _embed_batch(client, model: str, part: list[str], dims: int) -> list[list[float]]:
    resp = _create(client, model, part)
    data = resp.data
    if len(data) != len(part):
        raise EmbedError("embedding response count mismatch")
    if all(getattr(d, "index", None) is not None for d in data):
        data = sorted(data, key=lambda d: d.index)
    out = []
    for d in data:
        if len(d.embedding) != dims:
            raise NonRetryable("embedding dimension mismatch")
        out.append(list(d.embedding))
    return out


def embed(
    client, model: str, texts: list[str], dims: int, batch: int = 32, concurrency: int = 1
) -> list[list[float]]:
    """Embeddings in input order. `concurrency` > 1 keeps that many requests of `batch` texts in flight (embedding
    replicas are the bottleneck of a re-embed: one request at a time leaves them idle). The first hard failure
    cancels every request not yet started and is re-raised. Sizing rule: reembed_slices x embed_concurrency
    (+ ingest workers x embed_concurrency) ~ replicas x per-replica concurrency."""
    parts = [texts[i : i + batch] for i in range(0, len(texts), batch)]
    if concurrency <= 1 or len(parts) <= 1:
        return [v for part in parts for v in _embed_batch(client, model, part, dims)]
    pool = ThreadPoolExecutor(max_workers=min(concurrency, len(parts)), thread_name_prefix="embed")
    try:
        futs = [pool.submit(_embed_batch, client, model, part, dims) for part in parts]
        wait(futs, return_when=FIRST_EXCEPTION)
        for f in futs:  # in order: the first failure (in input order among the finished) wins
            if f.done() and f.exception() is not None:
                raise f.exception()  # type: ignore[misc]
        return [v for f in futs for v in f.result()]
    finally:
        pool.shutdown(wait=True, cancel_futures=True)  # queued requests never start after a failure


def to_bytes(vec) -> bytes:
    return np.asarray(vec, dtype="<f4").tobytes()


def from_bytes(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype="<f4")
