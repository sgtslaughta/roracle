"""/search first-stage page cache: per-principal, short TTL, slim entries.

Only ES results are cached; the Postgres visibility re-check (R19) runs on every request. The cache is per
process (page 1 and page 2 served by different workers may come from different first-stage snapshots).

Memory bound: at most PAGE_CACHE_MAX entries; each keeps the full passage (text, first bbox, highlight) of only
the PASSAGES_PER_DOC best passages per document (the only ones a page can show) and a bare id/score stub for
the rest, so an entry is ~100 docs x 2 passages x ~3 KB ~ 0.6 MB: ~40 MB worst case per worker."""

from dataclasses import replace
from typing import Any

from roracle.auth import Principal
from roracle.config import Settings
from roracle.labels.policy import Policy
from roracle.retrieval import PASSAGES_PER_DOC, Passage
from roracle.retrieval_util import TtlLru
from roracle.search_index import active_index

PAGE_CACHE = TtlLru()
PAGE_CACHE_MAX = 64


def cache_key(
    p: Principal, settings: Settings, q: str, filters: dict, coll_docs, light: bool, policy: Policy
) -> tuple[Any, ...]:
    """Everything that shapes the first-stage result: the principal (never shared across principals), the query,
    filters and collection scope, the label policy (exact labels tuple: a relabel/gate change is a miss) and the
    search configuration (active index = re-embed cutover, rerank and similarity settings)."""
    return (
        p.user_id,
        tuple(sorted(p.groups)),
        p.via,
        p.is_admin,
        q,
        tuple(sorted((k, tuple(sorted(map(str, v)))) for k, v in filters.items() if v)),
        None if coll_docs is None else tuple(sorted(coll_docs)),
        light,
        policy.labels,
        active_index(settings),
        settings.rerank_url,
        settings.rerank_blend,
        settings.search_rerank_depth,
        settings.search_min_similarity,
    )


def slim(passages: list[Passage]) -> list[Passage]:
    """Copies safe to keep: full content for the best PASSAGES_PER_DOC per document, id/score stubs for the rest
    (`passages` is best-first, as `group_by_document` expects)."""
    seen: dict[str, int] = {}
    out = []
    for x in passages:
        n = seen[x.doc_id] = seen.get(x.doc_id, 0) + 1
        if n <= PASSAGES_PER_DOC:
            out.append(replace(x, bboxes=x.bboxes[:1], source_chunks=[]))
        else:
            out.append(replace(x, text="", highlight="", bboxes=[], source_chunks=[], authors=[]))
    return out


def rehydrate(cached: list[Passage]) -> list[Passage]:
    """Fresh objects per request (callers set scores/notes on them); the shared lists are never mutated."""
    return [replace(x) for x in cached]
