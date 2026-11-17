import pytest
from api_seed import FakeES, seed

from roracle.api.routes import search as search_routes
from roracle.api.routes.library import get_es

pytestmark = pytest.mark.usefixtures("_blob_store_override")


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
    monkeypatch.setattr(search_routes, "MAX_DOCS", 4)
    r = client.get("/search", headers=as_bob, params={"size": 2}).json()
    assert r["total"] == 4 and r["next"] is not None


def test_after_only_for_newest_first_browse(client, as_bob, browse):
    cur = "2026-01-01T00:00:00+00:00,00000000-0000-0000-0000-000000000000"
    assert client.get("/search", headers=as_bob, params={"after": "junk"}).status_code == 422
    assert client.get("/search", headers=as_bob, params={"after": "2026-01-01T00:00:00,x"}).status_code == 422
    assert client.get("/search", headers=as_bob, params={"after": cur, "q": "x"}).status_code == 422
    assert client.get("/search", headers=as_bob, params={"after": cur, "sort": "title"}).status_code == 422
    assert client.get("/search", headers=as_bob, params={"after": cur}).status_code == 200


def test_after_never_widens_visibility(client, as_bob, ana, db_session):
    client.app.dependency_overrides[get_es] = lambda: FakeES()
    seed(db_session, ana, title="open", hours=1)
    seed(db_session, ana, title="secret", visibility="private", hours=2)
    r = client.get(
        "/search",
        headers=as_bob,
        params={"after": "2027-01-01T00:00:00+00:00,ffffffff-0000-0000-0000-000000000000"},
    )
    assert titles(r) == ["open"]


def test_rows_never_touch_deferred_version_columns(browse, db_session, ana):
    """ROW_COLUMNS + raiseload: reading a column outside the list would raise, so rendering every hit proves it."""
    from roracle.auth import Principal

    p = Principal(user_id=ana, sub="s", username="u", groups=(), is_admin=True)
    rows = search_routes._load(db_session, p, None, None, 50)
    assert len(rows) == 7
    for r in rows:
        search_routes._hit(r)
        r.facet_values()
    from sqlalchemy.exc import InvalidRequestError

    with pytest.raises(InvalidRequestError):
        _ = rows[0].ver.minhash
