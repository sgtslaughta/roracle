import pytest
from elasticsearch import BadRequestError

from roracle.search_index import cached_vectors


class _Meta:
    status = 400


class _FakeES:
    def __init__(self, msg):
        self.msg = msg

    def search(self, **_):
        raise BadRequestError(self.msg, _Meta(), {"error": {"reason": self.msg}})


def test_no_mapping_found_returns_empty():
    es = _FakeES("no mapping found for `prompt_sha` in order to collapse on")
    assert cached_vectors(es, "m", "d", ["a"], 4) == {}


def test_other_bad_request_raises():
    with pytest.raises(BadRequestError):
        cached_vectors(_FakeES("something else"), "m", "d", ["a"], 4)
