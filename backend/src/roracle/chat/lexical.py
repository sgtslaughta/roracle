"""The chat BM25 leg: the filler-free question (`lexical_query`) over field-boosted chunk fields.

Uses only fields chunks already carry, no reindex: `title` and `tags` (every chunk, kept in sync by acl_sync),
`abstract` (head chunk, `search_fields` >= 1; an index without it simply never matches the field), and the
chunk's own `text`/`headings`/`context`. Dense kNN and the reranker keep the raw question.
"""

import re
from typing import Any

from roracle.chat.intent import lexical_query
from roracle.retrieval import UNIT_BM25_FACTOR, UNIT_KINDS

TITLE_BOOST = 3.0
TAG_BOOST = 3.0  # an exact tag is as strong as a title word
TAG_PREFIX_BOOST = 1.5  # "robots" -> tag "robotics": weaker, a prefix can over-reach
ABSTRACT_BOOST = 2.0
TIE_BREAKER = 0.3  # a chunk matching title AND body beats one matching the title alone
PREFIX_MIN = 5  # shorter stems ("art", "cell") prefix-match too many tags
MAX_TAG_WORDS = 8
FIELDS = ["text", f"title^{TITLE_BOOST:g}", f"abstract^{ABSTRACT_BOOST:g}", "headings", "context"]


def _tag(s: str) -> str:
    """Same shape as stored tags (`normalize_tag`): casefolded, non-word runs -> '-'."""
    return re.sub(r"[\W_]+", "-", s.casefold()).strip("-")


def _tag_clauses(q: str) -> list[dict[str, Any]]:
    words = [t for t in map(_tag, q.split()) if t][:MAX_TAG_WORDS]
    if not words:
        return []
    exact = list(dict.fromkeys([*words, _tag(q)]))
    stems = dict.fromkeys(w[:-1] if w.endswith("s") and not w.endswith("ss") else w for w in words)
    return [
        {"terms": {"tags": exact, "boost": TAG_BOOST}},
        *(
            {"prefix": {"tags": {"value": s, "boost": TAG_PREFIX_BOOST}}}
            for s in stems
            if len(s) >= PREFIX_MIN
        ),
    ]


def chat_bm25(question: str, units: tuple[str, ...]) -> dict[str, Any]:
    q = lexical_query(question)
    mm = {"multi_match": {"query": q, "fields": FIELDS, "tie_breaker": TIE_BREAKER}}
    clause: dict[str, Any] = {"bool": {"should": [mm, *_tag_clauses(q)], "minimum_should_match": 1}}
    if not any(u in UNIT_KINDS for u in units):
        return clause
    return {
        "boosting": {
            "positive": clause,
            "negative": {"exists": {"field": "unit"}},
            "negative_boost": UNIT_BM25_FACTOR,
        }
    }
