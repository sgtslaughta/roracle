"""Query-path post-filters (votes, visible cited-by) and in-memory sorts mirroring `ES_SORTS` (R1, R2, R9)."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from roracle.api.routes.search_load import Row
from roracle.search_params import SearchParams, Sort
from roracle.search_post import post_filter, sort_rows


def row(title, *, year, venue, pages, authors, day, score, votes, version=1) -> Row:
    doc = SimpleNamespace(
        id=uuid.uuid4(), title=title, created_at=datetime(2026, 1, day, tzinfo=UTC), owner_id=None
    )
    ver = SimpleNamespace(
        meta={"year": year, "venue": venue, "authors": authors}, page_count=pages, filename="x.pdf",
        version=version,
    )  # fmt: skip
    return Row(doc, ver, votes=votes, score=score)  # type: ignore[arg-type]


@pytest.fixture
def rows():
    b = row("beta", year=2020, venue="Xen", pages=10, authors=["Bo"], day=1, score=0.5, votes=(3, 1, 0))
    a = row("Alpha", year=None, venue=None, pages=None, authors=[], day=3, score=0.9, votes=(0, 0, 0))
    c = row("gamma", year=2021, venue="arch", pages=3, authors=["al"], day=2, score=0.1, votes=(1, 0, 0))
    return [b, a, c]


def cited_of(rows):
    return {str(rows[0].doc.id): 5, str(rows[2].doc.id): 2}  # Alpha: no visible citer


@pytest.mark.parametrize(
    ("sort", "want"),
    [
        ("relevance", ["Alpha", "beta", "gamma"]),
        ("newest", ["Alpha", "gamma", "beta"]),
        ("published_desc", ["gamma", "beta", "Alpha"]),
        ("published_asc", ["beta", "gamma", "Alpha"]),
        ("votes", ["beta", "gamma", "Alpha"]),
        ("cited", ["beta", "gamma", "Alpha"]),
        ("title", ["Alpha", "beta", "gamma"]),
        ("first_author", ["gamma", "beta", "Alpha"]),  # casefolded: "al" < "bo"; no author last
        ("venue", ["gamma", "beta", "Alpha"]),  # "arch" < "xen"; missing last
        ("pages", ["gamma", "beta", "Alpha"]),  # ascending, missing last
    ],
)
def test_sort_rows_every_sort(rows, sort: Sort, want):
    sort_rows(rows, sort, cited_of(rows))
    assert [r.title for r in rows] == want


def test_every_sort_is_covered():
    assert set(Sort.__args__) == {
        "relevance", "newest", "published_desc", "published_asc", "votes", "cited", "title", "first_author",
        "venue", "pages",
    }  # fmt: skip


@pytest.mark.parametrize("sort", list(Sort.__args__))
def test_ties_keep_input_order(sort: Sort):
    same = {"year": 2020, "venue": "V", "pages": 4, "authors": ["A"], "day": 1, "score": 0.5}
    rs = [row("same", **same, votes=(1, 0, 0)) for _ in range(3)]  # type: ignore[arg-type]
    want = [r.doc.id for r in rs]
    sort_rows(rs, sort, {str(r.doc.id): 1 for r in rs})
    assert [r.doc.id for r in rs] == want


def test_missing_values_keep_input_order_too(rows):
    blank = row("blank", year=None, venue=None, pages=None, authors=[], day=1, score=0.5, votes=(0, 0, 0))
    rs = [rows[1], blank]  # both miss year/venue/pages/author
    for s in ("published_desc", "published_asc", "venue", "pages", "first_author"):
        sort_rows(rs, s, {})
        assert [r.title for r in rs] == ["Alpha", "blank"], s


def test_post_filter(rows):
    cited = cited_of(rows)
    assert post_filter(rows, SearchParams(), cited) == rows
    titles = lambda **kw: [r.title for r in post_filter(rows, SearchParams(**kw), cited)]
    assert titles(votes_min=1) == ["beta", "gamma"]  # up - down: 2, 0, 1
    assert titles(votes_min=2) == ["beta"]
    assert titles(cited_min=2) == ["beta", "gamma"]
    assert titles(cited_min=3, votes_min=1) == ["beta"]
    assert titles(votes_min=-1) == ["beta", "Alpha", "gamma"]
