"""Query-path post-filters and sorts over the Postgres-rechecked candidates (R1, R2): votes and cited-by come from
Postgres (`cited` = `cited_by_counts`, visible citers only), sorts mirror `ES_SORTS` keys and missing-last rules."""

from collections.abc import Callable
from typing import Any

from roracle.api.routes.search_load import Row
from roracle.search_fields import sort_key
from roracle.search_params import SearchParams, Sort


def post_filter(rows: list[Row], f: SearchParams, cited: dict[str, int]) -> list[Row]:
    return [
        r
        for r in rows
        if (f.votes_min is None or r.votes[0] - r.votes[1] >= f.votes_min)
        and (f.cited_min is None or cited.get(str(r.doc.id), 0) >= f.cited_min)
    ]


def _year(r: Row) -> int | None:
    try:
        return int(r.meta.get("year"))  # ES coerces "2020" the same way
    except (TypeError, ValueError):
        return None


def _str(v: Any) -> str | None:
    return sort_key(v) if isinstance(v, str) else None


def _keys(cited: dict[str, int]) -> dict[Sort, tuple[Callable[[Row], Any], bool]]:
    """(key, descending) per sort; a None key sorts last either way."""
    return {
        "relevance": (lambda r: r.score, True),
        "newest": (lambda r: r.doc.created_at, True),  # added_at (R8)
        "published_desc": (_year, True),
        "published_asc": (_year, False),
        "votes": (lambda r: r.votes[0] - r.votes[1], True),
        "cited": (lambda r: cited.get(str(r.doc.id), 0), True),
        "title": (lambda r: sort_key(r.title), False),
        "first_author": (lambda r: _str(next(iter(r.meta.get("authors") or []), None)), False),
        "venue": (lambda r: _str(r.meta.get("venue")), False),
        "pages": (lambda r: r.ver.page_count, False),  # shortest first (R9)
    }


def sort_rows(rows: list[Row], sort: Sort, cited: dict[str, int]) -> None:
    """Stable and in place: ties keep the input (relevance) order."""
    key, desc = _keys(cited)[sort]
    have = [r for r in rows if key(r) is not None]
    have.sort(key=key, reverse=desc)  # reverse=True keeps equal elements in input order
    rows[:] = have + [r for r in rows if key(r) is None]
