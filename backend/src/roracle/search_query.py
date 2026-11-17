"""Library-search query syntax (R6) and field scope (R7): `"phrase"`, `-exclude`, `-"phrase"`."""

import re
from dataclasses import dataclass
from typing import Any

from roracle.retrieval import UNIT_BM25_FACTOR, UNIT_KINDS
from roracle.search_limits import BadInput
from roracle.search_params import Scope

SCOPE_FIELDS: dict[Scope, list[str]] = {
    "anywhere": ["text^1", "title^2", "headings", "context"],
    "title": ["title"],
    "authors": ["authors_text"],
    "abstract": ["abstract"],
    "fulltext": ["text", "headings", "context"],
}
_TOKEN = re.compile(r'(-?)"([^"]*)"|(\S+)')


@dataclass(frozen=True)
class ParsedQuery:
    words: str
    phrases: tuple[str, ...]
    excluded: tuple[str, ...]


def parse_query(q: str) -> ParsedQuery:
    """Quotes pair up left to right; a lone `-` or an unbalanced quote stays a plain character."""
    words: list[str] = []
    phrases: list[str] = []
    excluded: list[str] = []
    for m in _TOKEN.finditer(q):
        neg, quoted, bare = m.groups()
        if bare is not None:
            if bare.startswith("-") and len(bare) > 1 and '"' not in bare:
                excluded.append(bare[1:])
            else:
                words.append(bare)
        elif (term := " ".join(quoted.split())) and neg:
            excluded.append(term)
        elif term:
            phrases.append(term)
    if not words and not phrases:
        raise BadInput("add at least one word to search for")
    return ParsedQuery(" ".join(words), tuple(phrases), tuple(excluded))


def head_only(scope: Scope) -> bool:
    return scope in ("title", "authors", "abstract")


def bm25_clause(pq: ParsedQuery, scope: Scope, units: tuple[str, ...]) -> dict[str, Any]:
    mm = {"multi_match": {"query": pq.words or " ".join(pq.phrases), "fields": SCOPE_FIELDS[scope]}}
    if not any(u in UNIT_KINDS for u in units):
        return mm
    return {
        "boosting": {
            "positive": mm,
            "negative": {"exists": {"field": "unit"}},
            "negative_boost": UNIT_BM25_FACTOR,
        }
    }


def text_clauses(pq: ParsedQuery, scope: Scope) -> tuple[list[dict], list[dict]]:
    """(filter, must_not): phrases must match; excluded terms must not."""
    fields = SCOPE_FIELDS[scope]
    flt = [{"multi_match": {"query": p, "type": "phrase", "fields": fields}} for p in pq.phrases]
    nn = []
    for e in pq.excluded:
        mm: dict[str, Any] = {"query": e, "fields": fields}
        if " " in e:
            mm["type"] = "phrase"
        nn.append({"multi_match": mm})
    return flt, nn


def embed_text(pq: ParsedQuery) -> str:
    return " ".join(x for x in (pq.words, *pq.phrases) if x)
