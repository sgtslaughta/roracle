from datetime import UTC, datetime
from uuid import UUID

import pytest
from api_seed import FakeES, seed

from roracle import search_legacy
from roracle.api.routes import search_load
from roracle.api.routes.library import get_es
from roracle.cursor import make_cursor

pytestmark = pytest.mark.usefixtures("_blob_store_override")
# FakeES reports an index without the search-fields marker: every request here runs `legacy_search` (R5).


@pytest.fixture
def fes(app):
    f = FakeES()
    app.dependency_overrides[get_es] = lambda: f
    return f


def get(client, headers, **params):
    return client.get("/search", headers=headers, params=params)


@pytest.fixture
def browse(client, as_bob, ana, db_session):
    client.app.dependency_overrides[get_es] = lambda: FakeES()
    docs = [seed(db_session, ana, title=f"D{i}", hours=i)[0] for i in range(7)]  # D6 is the newest
    return docs


def titles(r):
    return [x["title"] for x in r.json()["results"]]


def test_keyset_pages_walk_the_same_order_as_page_numbers(client, as_bob, browse):
    full = client.get("/search", headers=as_bob, params={"size": 50}).json()
    assert [x["title"] for x in full["results"]] == [f"D{i}" for i in (6, 5, 4, 3, 2, 1, 0)]
    assert full["next"] is None and full["total"] == 7
    seen, after = [], None
    for _ in range(4):
        r = client.get("/search", headers=as_bob, params={"size": 3, **({"after": after} if after else {})})
        assert r.status_code == 200
        seen += titles(r)
        after = r.json()["next"]
        if after is None:
            break
    assert seen == [x["title"] for x in full["results"]]


def test_response_total_stays_capped(client, as_bob, browse, monkeypatch):
    monkeypatch.setattr(search_legacy, "MAX_DOCS", 4)
    r = client.get("/search", headers=as_bob, params={"size": 2}).json()
    assert r["total"] == 4 and r["next"] is not None


def test_after_only_for_newest_first_browse(client, as_bob, browse):
    cur = make_cursor(datetime(2026, 1, 1, tzinfo=UTC), UUID(int=0))
    assert client.get("/search", headers=as_bob, params={"after": "junk"}).status_code == 422
    assert client.get("/search", headers=as_bob, params={"after": "2026-01-01T00:00:00,x"}).status_code == 422
    assert client.get("/search", headers=as_bob, params={"after": cur, "page": 2}).status_code == 422
    assert client.get("/search", headers=as_bob, params={"after": cur, "q": "x"}).status_code == 422
    assert client.get("/search", headers=as_bob, params={"after": cur, "sort": "title"}).status_code == 422
    ok = client.get("/search", headers=as_bob, params={"after": cur})
    assert (
        ok.status_code == 200 and ok.json()["window"] is True
    )  # total/facets describe the window after the cursor
    assert "+" not in cur and "," not in cur  # opaque and URL-safe


def test_after_never_widens_visibility(client, as_bob, ana, db_session):
    client.app.dependency_overrides[get_es] = lambda: FakeES()
    seed(db_session, ana, title="open", hours=1)
    seed(db_session, ana, title="secret", visibility="private", hours=2)
    r = client.get(
        "/search",
        headers=as_bob,
        params={"after": make_cursor(datetime(2027, 1, 1, tzinfo=UTC), UUID(int=2**128 - 1))},
    )
    assert titles(r) == ["open"]


def test_rows_never_touch_deferred_version_columns(browse, db_session, ana):
    """ROW_COLUMNS + raiseload: reading a column outside the list would raise, so rendering every hit proves it."""
    from roracle.auth import Principal

    p = Principal(user_id=ana, sub="s", username="u", groups=(), is_admin=True)
    rows = search_load._load(db_session, p, None, None, 50)
    assert len(rows) == 7
    for r in rows:
        search_load._hit(r)
        r.facet_values()
    from sqlalchemy.exc import InvalidRequestError

    with pytest.raises(InvalidRequestError):
        _ = rows[0].ver.minhash


def test_paging(client, as_ana, ana, db_session, fes):
    for i in range(5):
        seed(db_session, ana, title=f"t{i}", hours=i)
    r = get(client, as_ana, q="", sort="newest", size=2, page=2).json()
    assert r["total"] == 5 and r["page"] == 2 and [x["title"] for x in r["results"]] == ["t2", "t1"]
    assert r["upgrading"] is True  # FakeES index has no search-fields marker
    assert get(client, as_ana, q="", size=2, page=9).json()["results"] == []


def test_empty_q_browses_without_es(client, as_ana, ana, bob, db_session, fes):
    seed(db_session, ana, title="old", hours=1)
    seed(db_session, bob, title="new", hours=5)
    seed(db_session, bob, title="hidden", visibility="private", hours=9)
    seed(db_session, ana, title="parsing", stage="parsed", hours=8)
    for q in ("", "   "):
        r = get(client, as_ana, q=q).json()
        assert [x["title"] for x in r["results"]] == ["new", "old"] and r["total"] == 2
        assert r["results"][0]["passages"] == []
    assert get(client, as_ana).json()["total"] == 2  # q omitted
    assert fes.calls == []


@pytest.mark.parametrize("params", [{"author": ["Zed"]}, {"file_type": ["docx"]}, {"file_type": ["other"]}])
def test_browse_filters_keep_limit(client, as_ana, ana, db_session, fes, monkeypatch, params):
    seed(db_session, ana, title="Z", authors=("Zed",), filename="z.docx")
    seed(db_session, ana, title="N", authors=("Ann",), filename="noext")
    limits = []
    real = search_legacy._load

    def spy(*a, **k):
        limits.append(a[4] if len(a) > 4 else k.get("limit"))
        return real(*a, **k)

    monkeypatch.setattr(search_legacy, "_load", spy)
    get(client, as_ana, **params)
    assert limits[0] == search_legacy.MAX_DOCS


def test_browse_author_file_type_sql_semantics(client, as_ana, ana, db_session, fes):
    seed(db_session, ana, title="A", authors=("Ann", "Bo"), filename="a.PDF")
    seed(db_session, ana, title="B", authors=("Cy",), filename="b.pdf.docx")
    seed(db_session, ana, title="C", authors=("Bo",), filename="c.toolongext1")
    t = lambda **p: sorted(x["title"] for x in get(client, as_ana, **p).json()["results"])
    assert t(author=["Bo"]) == ["A", "C"]
    assert t(author=["Bo", "Cy"]) == ["A", "B", "C"]
    assert t(file_type=["pdf"]) == ["A"]
    assert t(file_type=["pdf", "docx"]) == ["A", "B"]
    assert t(file_type=["other"]) == ["C"]
    assert t(author=["Bo"], file_type=["pdf"]) == ["A"]
