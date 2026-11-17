from uuid import uuid4

import pytest

from roracle.auth import Principal
from roracle.search_filters import plain_clauses
from roracle.search_ids import id_kinds, issns
from roracle.search_params import SearchParams
from roracle.search_scope import DocScope


@pytest.fixture
def principal():
    return Principal(user_id=uuid4(), sub="s", username="u", groups=(), is_admin=False)


@pytest.mark.parametrize("meta,want", [
    ({}, []), ({"ids": None}, []), ({"ids": "junk"}, []),
    ({"ids": {"issn": "0028-0836", "eissn": "1476-4687"}}, ["0028-0836", "1476-4687"]),
    ({"ids": {"issn": "1234-567x", "eissn": "1234-567X"}}, ["1234-567X"]),
    ({"ids": {"issn": "12345678"}}, []),
])  # fmt: skip
def test_issns(meta, want):
    assert issns(meta) == want


def test_id_kinds_legacy_doi_and_eissn():
    meta = {"doi": "10.1/x", "ids": {"eissn": "1476-4687", "arxiv": "", "pmid": "123"}}
    assert id_kinds(meta) == ["doi", "issn", "pmid"]
    assert id_kinds(None) == []


def test_id_kinds_malformed_issn_is_no_journal():
    """R15: a malformed ISSN contributes nothing, so has_id=issn never matches a journal-less paper."""
    assert id_kinds({"ids": {"issn": "12345678", "eissn": "junk", "pmid": "1"}}) == ["pmid"]
    assert id_kinds({"ids": {"issn": "junk", "eissn": "1476-4687"}}) == ["issn"]


def test_journal_values_validated_and_exclusion_wins():
    f = SearchParams(journal=["0028-0836", "nope"], journal_not=["0028-0836"])
    assert f.journal == [] and f.journal_not == ["0028-0836"]


def test_has_id_clauses(principal):
    flt, nn = plain_clauses(
        SearchParams(has_id=["arxiv", "pmid"], has_id_not=["doi"]), principal, DocScope(None, [], {})
    )
    assert {"terms": {"ids_present": ["arxiv", "pmid"]}} in flt
    assert {"terms": {"ids_present": ["doi"]}} in nn


def test_has_id_exclusion_wins():
    f = SearchParams(has_id=["doi", "arxiv"], has_id_not=["doi"])
    assert f.has_id == ["arxiv"] and f.has_id_not == ["doi"]
