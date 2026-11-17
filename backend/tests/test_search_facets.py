import pytest

from roracle.labels.policy import LabelInfo, Policy, label_json
from roracle.search_acl import acl_filter  # noqa: F401  (ACL is the caller's `filter`, never built here)
from roracle.search_facets import ci_regex, facet_aggs, find_aggs, read_facets
from roracle.search_limits import FACET_MAX
from roracle.search_params import SearchParams

LBL = LabelInfo("L1", "Secret", "#f00", "", 2, False, True, False, False, False)


def test_each_facet_skips_itself():
    a = facet_aggs(SearchParams(tags=["a"], venue=["V"]), {})
    assert a["f_tags"]["filter"] == {"bool": {"filter": [{"terms": {"venue": ["V"]}}], "must_not": []}}
    assert a["f_venue"]["filter"] == {"bool": {"filter": [{"terms": {"tags": ["a"]}}], "must_not": []}}
    assert a["f_tags"]["aggs"]["v"]["terms"] == {"field": "tags", "size": FACET_MAX}
    assert a["f_author"]["aggs"]["v"]["terms"]["field"] == "authors"
    assert a["f_label"]["aggs"]["v"]["terms"]["field"] == "label_id"


def test_year_is_histogram():
    a = facet_aggs(SearchParams(), {})
    assert a["f_year"]["aggs"]["v"]["histogram"] == {"field": "year", "interval": 1, "min_doc_count": 1}


def test_collection_filters_agg_and_other_facets():
    a = facet_aggs(SearchParams(tags=["a"]), {"c1": ("Mine", ["d1", "d2"]), "c2": ("Other", [])})
    c = a["f_collection"]
    assert c["filter"]["bool"]["filter"] == [{"terms": {"tags": ["a"]}}]
    assert c["aggs"]["v"]["filters"]["filters"] == {
        "c1": {"terms": {"doc_id": ["d1", "d2"]}},
        "c2": {"terms": {"doc_id": []}},
    }


@pytest.mark.parametrize(
    "s,want",
    [
        ("smi", ".*[sS][mM][iI].*"),
        ("smith", ".*[sS][mM][iI][tT][hH].*"),
        ("Ñúñez", ".*[ñÑ][úÚ][ñÑ][eE][zZ].*"),
        ("O'B (ed.)", ".*[oO]'[bB] \\([eE][dD]\\.\\).*"),
        ("O'Brien (ed.)", ".*[oO]'[bB][rR][iI][eE][nN] \\([eE][dD]\\.\\).*"),
        (".*", ".*\\.\\*.*"),
        ('a"b\\c#@&<>~', '.*[aA]\\"[bB]\\\\[cC]\\#\\@\\&\\<\\>\\~.*'),
    ],
)
def test_ci_regex(s, want):
    assert ci_regex(s) == want


def test_find_aggs():
    a = find_aggs("author", "Smi", SearchParams(tags=["a"], author=["X"]))
    assert a["filter"] == {"bool": {"filter": [{"terms": {"tags": ["a"]}}], "must_not": []}}
    assert a["aggs"]["v"]["terms"] == {"field": "authors", "include": ci_regex("Smi"), "size": FACET_MAX}


def test_read_facets_shapes():
    policy = Policy([LBL])
    aggs = {
        "f_year": {"v": {"buckets": [{"key": 2021.0, "doc_count": 1}, {"key": 2019.0, "doc_count": 5}]}},
        "f_tags": {
            "v": {
                "buckets": [
                    {"key": "b", "doc_count": 2},
                    {"key": "a", "doc_count": 2},
                    {"key": "z", "doc_count": 9},
                ]
            }
        },
        "f_label": {"v": {"buckets": [{"key": "L1", "doc_count": 3}]}},
        "f_collection": {
            "v": {"buckets": {"c1": {"doc_count": 4}, "c2": {"doc_count": 0}}},
        },
    }
    out = read_facets(aggs, policy, {"c1": ("Mine", []), "c2": ("Other", [])})
    assert [e["value"] for e in out["year"]] == ["2019", "2021"]
    assert out["year"][0]["count"] == 5
    assert [(e["value"], e["count"]) for e in out["tags"]] == [("z", 9), ("a", 2), ("b", 2)]
    assert out["label"] == [{"value": "L1", "count": 3, "label": label_json(LBL)}]
    assert out["collection"] == [{"value": "c1", "count": 4, "label": "Mine"}]
    assert out["venue"] == []
