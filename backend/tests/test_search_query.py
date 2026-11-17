"""Query syntax parser and field-scoped clause builders (R6/R7)."""

import pytest

from roracle.search_limits import BadInput
from roracle.search_query import ParsedQuery, bm25_clause, embed_text, head_only, parse_query, text_clauses


@pytest.mark.parametrize("q,want", [
    ('graph "neural network" -survey', ParsedQuery("graph", ("neural network",), ("survey",))),
    ('-"related work" transformers', ParsedQuery("transformers", (), ("related work",))),
    ('state-of-the-art "unclosed', ParsedQuery('state-of-the-art "unclosed', (), ())),
    ('"only a phrase"', ParsedQuery("", ("only a phrase",), ())),
    ("a - b", ParsedQuery("a - b", (), ())),
])  # fmt: skip
def test_parse(q, want):
    assert parse_query(q) == want


@pytest.mark.parametrize("q", ["-a -b", '""', "   "])
def test_needs_a_positive_term(q):
    with pytest.raises(BadInput, match="add at least one word"):
        parse_query(q)


def test_scope_fields_and_head_only():
    assert bm25_clause(parse_query("x"), "abstract", ("chunk",))["multi_match"]["fields"] == ["abstract"]
    assert head_only("authors") and not head_only("fulltext")


def test_bm25_phrase_only_and_summary_boosting():
    c = bm25_clause(parse_query('"a b"'), "anywhere", ("chunk", "document"))
    assert c["boosting"]["positive"]["multi_match"]["query"] == "a b"


def test_text_clauses():
    flt, nn = text_clauses(parse_query('a "b c" -d'), "title")
    assert flt == [{"multi_match": {"query": "b c", "type": "phrase", "fields": ["title"]}}]
    assert nn == [{"multi_match": {"query": "d", "type": "phrase", "fields": ["title"]}}]


def test_hyphenated_exclusion_is_a_phrase():
    _, nn = text_clauses(parse_query("a -state-of-the-art"), "anywhere")
    assert nn[0]["multi_match"]["query"] == "state-of-the-art" and nn[0]["multi_match"]["type"] == "phrase"


def test_embed_text_drops_exclusions():
    assert embed_text(parse_query('graph "neural network" -survey')) == "graph neural network"
