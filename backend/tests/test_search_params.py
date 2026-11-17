from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from roracle.auth import Principal
from roracle.search_filters import facet_clauses, head_clauses, plain_clauses
from roracle.search_params import SearchParams, cache_tuple, uses_new_params
from roracle.search_scope import DocScope

U = uuid4()


@pytest.fixture
def principal():
    return Principal(user_id=uuid4(), sub="s", username="u", groups=(), is_admin=False)


def test_exclusion_wins():
    f = SearchParams(tags=["ml", "cv"], tags_not=["ml"])
    assert f.tags == ["cv"] and f.tags_not == ["ml"]


@pytest.mark.parametrize(
    "bad",
    [
        {"year_min": 2024, "year_max": 2018},
        {"pages_min": 9, "pages_max": 1},
        {"added_min": "2025-02-01", "added_max": "2025-01-01"},
    ],
)
def test_inverted_ranges_rejected(bad):
    with pytest.raises(ValidationError, match="must be <="):
        SearchParams(**bad)


def test_unknown_key_rejected():
    with pytest.raises(ValidationError):
        SearchParams(colour="red")


def test_label_keeps_uuids_only():
    assert SearchParams(label=["x", str(U)]).label == [str(U)]
    assert SearchParams(label_not=["x", str(U)]).label_not == [str(U)]


def test_and_or_clauses():
    flt, _ = facet_clauses(SearchParams(author=["A", "B"], author_op="and"))
    assert {"term": {"authors": "A"}} in flt and {"term": {"authors": "B"}} in flt
    flt, nn = facet_clauses(SearchParams(tags=["a", "b"], tags_not=["c"]))
    assert {"terms": {"tags": ["a", "b"]}} in flt and nn == [{"terms": {"tags": ["c"]}}]


def test_skip_leaves_one_facet_out():
    flt, _ = facet_clauses(SearchParams(tags=["a"], venue=["V"]), skip="tags")
    assert flt == [{"terms": {"venue": ["V"]}}]


def test_year_range_is_a_facet():
    assert facet_clauses(SearchParams(year_min=2018, year_max=2024))[0] == [
        {"range": {"year": {"gte": 2018, "lte": 2024}}}
    ]
    assert facet_clauses(SearchParams(year_min=2018), skip="year")[0] == []


def test_owner_shared(principal):
    _, nn = plain_clauses(SearchParams(owner="shared"), principal, DocScope(None, [], {}))
    assert {"term": {"owner_id": str(principal.user_id)}} in nn


def test_plain_has_and_scope(principal):
    flt, nn = plain_clauses(
        SearchParams(has_doi="yes", has_refs="no", owner="mine", pages_min=3),
        principal,
        DocScope(["a"], ["b"], {}),
    )
    assert {"term": {"has_doi": True}} in flt and {"term": {"has_refs": True}} in nn
    assert {"terms": {"doc_id": ["a"]}} in flt and nn[-1] == {"terms": {"doc_id": ["b"]}}
    assert {"range": {"page_count": {"gte": 3}}} in flt


def test_head_clauses():
    assert head_clauses(SearchParams(votes_min=3)) == [
        {"term": {"doc_head": True}},
        {"term": {"searchable": True}},
        {"range": {"votes_net": {"gte": 3}}},
    ]
    assert {"range": {"cited_by_global": {"gte": 2}}} in head_clauses(SearchParams(cited_min=2))


def test_uses_new_params():
    assert not uses_new_params(SearchParams(q="x", tags=["a"], sort="newest"))
    assert uses_new_params(SearchParams(tags_not=["a"])) and uses_new_params(SearchParams(sort="cited"))
    assert uses_new_params(SearchParams(year_min=2000)) and uses_new_params(SearchParams(**{"in": "title"}))


def test_cache_tuple_ignores_paging_and_sort():
    a = SearchParams(q="x", tags=["a"], page=3, sort="votes", size=5)
    assert cache_tuple(a) == cache_tuple(SearchParams(q="x", tags=["a"]))
    assert cache_tuple(a) != cache_tuple(SearchParams(q="x", tags=["b"]))
    hash(cache_tuple(SearchParams(cites=UUID(int=1), added_min="2025-01-01")))
