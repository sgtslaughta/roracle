"""Browse request shapes (no real ES): ACL in the top-level filter of EVERY request, top-ups, cursors, limits."""

import uuid

import pytest
from conftest import bearer
from test_labels_text import mk
from test_mcp_tools import pat

from roracle.api.routes.library import get_es
from roracle.cursor import make_sort_cursor, parse_sort_cursor
from roracle.search_browse import ES_SORTS
from roracle.search_params import Sort

pytestmark = pytest.mark.usefixtures("_blob_store_override")


class RecordingES:
    """Ready index; every page full of hits Postgres does not know (so every page is topped up)."""

    def __init__(self):
        self.calls: list[dict] = []
        self.indices = type(
            "I", (), {"get_mapping": lambda _, index: {index: {"mappings": {"_meta": {"search_fields": 1}}}}}
        )()

    def search(self, **kw):
        self.calls.append(kw)
        n = kw.get("size", 0)
        hits = [{"_source": {"doc_id": str(uuid.uuid4())}, "sort": [i, str(i)]} for i in range(n)]
        return {"hits": {"total": {"value": n, "relation": "eq"}, "hits": hits}, "aggregations": {}}


@pytest.fixture
def res(app):
    r = RecordingES()
    app.dependency_overrides[get_es] = lambda: r
    return r


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="root", username="root", roles=("roracle-admin",)))


def _acl_first(call: dict, blocked: str | None = None) -> None:
    first = call["query"]["bool"]["filter"][0]
    assert "visibility" in str(first)
    if blocked:
        assert first["bool"]["must_not"] == [{"terms": {"label_id": [blocked]}}]


def test_every_request_carries_acl(client, as_ana, ana, admin, db_session, res):
    sec = mk(client, admin, "NoAgent", 3, block_agents=True)
    tok = pat(db_session, ana, ["read"])
    for params in ({}, {"sort": "cited"}, {"cited_min": 2}, {"sort": "title", "size": 5}):
        assert client.get("/search", headers=tok, params=params).status_code == 200
    assert client.get("/search/facets/venue", headers=tok, params={"find": "x"}).status_code == 200
    assert res.calls
    for c in res.calls:
        _acl_first(c, sec)
    browse = [c for c in res.calls if c.get("size") == 10]  # size=5 page: 2 * 5 hits per ES request
    assert len(browse) == 3  # BROWSE_TRIES: first page + two top-ups
    assert "aggs" in browse[0] and all("aggs" not in c and "search_after" in c for c in browse[1:])


def test_jwt_principal_has_no_agent_gate(client, as_ana, ana, admin, db_session, res):
    mk(client, admin, "NoAgent", 3, block_agents=True)
    assert client.get("/search", headers=as_ana).status_code == 200
    first = res.calls[0]["query"]["bool"]["filter"][0]
    assert "label_id" not in str(first)


def test_paging_limits(client, as_ana, res):
    assert client.get("/search", headers=as_ana, params={"page": 1000, "size": 10}).status_code == 422
    assert client.get("/search", headers=as_ana, params={"page": 999, "size": 10}).status_code == 200
    assert client.get("/search", headers=as_ana, params={"after": "junk"}).status_code == 422
    cur = make_sort_cursor([1, "x", 3])
    r = client.get("/search", headers=as_ana, params={"after": cur, "page": 2})
    assert r.status_code == 422
    assert client.get("/search", headers=as_ana, params={"after": cur, "sort": "cited"}).status_code == 422
    assert client.get("/search", headers=as_ana, params={"year": 2020}).status_code == 422  # R10


def test_sort_cursor_roundtrip_and_rejects():
    vals = ["émile zola", 1735689600000, None, 2.5]
    cur = make_sort_cursor(vals)
    assert parse_sort_cursor(cur) == vals and "=" not in cur and "+" not in cur
    for bad in ("junk!", make_sort_cursor({"a": 1}), make_sort_cursor([]), make_sort_cursor([[1]])):  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            parse_sort_cursor(bad)


@pytest.mark.parametrize("sort", list(Sort.__args__))
def test_every_sort_ends_with_tiebreak(sort):
    assert ES_SORTS[sort][-2:] == [{"added_at": {"order": "desc"}}, {"doc_id": {"order": "asc"}}]
