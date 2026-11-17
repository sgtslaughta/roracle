"""One keep-alive `httpx.Client` per process for the reranker (connection and TLS reuse).

Created at app / worker startup (`init`, idempotent, race-free: not lazy per request) and closed on shutdown.
Callers that run without it (tests, CLI) fall back to a per-call client."""

import threading

import httpx

_client: httpx.Client | None = None
_lock = threading.Lock()


def init() -> httpx.Client:
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(limits=httpx.Limits(max_keepalive_connections=32))
        return _client


def get() -> httpx.Client | None:
    return _client


def close() -> None:
    global _client
    with _lock:
        c, _client = _client, None
    if c is not None:
        c.close()
