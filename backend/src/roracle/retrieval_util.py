"""Retrieval helpers: TTL-LRU, query-embedding memo and the single-round-trip `_msearch`."""

import threading
import time
from array import array
from collections import OrderedDict
from typing import Any

from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig
from elasticsearch import ApiError
from elasticsearch.exceptions import HTTP_EXCEPTIONS

from roracle.config import Settings
from roracle.ingest.embed import embed, prompt_sha, query_prompt


class TtlLru:
    """Small thread-safe LRU with a TTL (embedding memo; the search page cache uses the same type)."""

    def __init__(self) -> None:
        self._d: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any, ttl: float) -> Any | None:
        with self._lock:
            hit = self._d.get(key)
            if hit is None or time.monotonic() - hit[0] > ttl:
                self._d.pop(key, None)
                return None
            self._d.move_to_end(key)
            return hit[1]

    def put(self, key: Any, value: Any, max_entries: int) -> None:
        with self._lock:
            self._d[key] = (time.monotonic(), value)
            self._d.move_to_end(key)
            while len(self._d) > max(1, max_entries):
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


_EMBEDS = TtlLru()


def embed_query(llm, settings: Settings, query: str, owner: object = "") -> list[float]:
    """Query embedding, memoized per (model, dims, prompt digest). Entries are float32 arrays (3 KB at 768
    dims; 10k entries ~ 30 MB), so repeated and paged searches skip the call at a bounded memory cost.
    Keyed per `owner` (the requesting user): a hit is faster than a miss, so a shared memo would tell one user
    whether ANOTHER user recently searched the same text."""
    key = (str(owner), settings.embed_model, settings.embed_dims, prompt_sha(query_prompt(query)))
    if settings.embed_cache_size > 0 and (hit := _EMBEDS.get(key, settings.embed_cache_ttl_s)) is not None:
        return list(hit)
    [vec] = embed(
        llm,
        settings.embed_model,
        [query_prompt(query)],
        settings.embed_dims,
        retries=settings.embed_query_retries,
    )
    if settings.embed_cache_size > 0:
        _EMBEDS.put(key, array("f", vec), settings.embed_cache_size)
    return vec


def _msearch(es, index: str, bodies: list[dict]) -> list[dict | ApiError]:
    """ONE round trip for several searches. A failed sub-search comes back as its `ApiError` subclass (not
    raised): the caller decides (e.g. the optional summary kNN is skipped on 400) and anything else re-raises
    the same type a plain `es.search` would have (404 NotFoundError, 429/5xx ApiError -> 503 upstream)."""
    searches: list[dict] = []
    for b in bodies:
        searches += [{}, b]
    out: list[dict | ApiError] = []
    for r in es.msearch(index=index, searches=searches)["responses"]:
        if "error" in r:
            status = int(r.get("status", 500))
            meta = ApiResponseMeta(status, "1.1", HttpHeaders(), 0.0, NodeConfig("http", "es", 9200))
            out.append(HTTP_EXCEPTIONS.get(status, ApiError)(str(r["error"])[:300], meta, r["error"]))
        else:
            out.append(r)
    return out
