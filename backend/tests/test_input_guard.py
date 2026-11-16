import pytest
from conftest import bearer  # noqa: F401

BIG = "99999999999999999999"


@pytest.mark.parametrize(
    "path,body",
    [
        ("/collections", {"name": "a\u0000b"}),
        ("/chats", {"title": "a\u0000b"}),
        ("/me/tokens", {"name": "a\u0000b", "scopes": ["read"], "days": 7}),
        ("/drafts", {"title": "a\u0000b"}),
    ],
)
def test_nul_in_json_is_422(client, as_ana, path, body):
    assert client.post(path, headers=as_ana, json=body).status_code == 422


def test_escaped_backslash_u0000_is_not_nul(client, as_ana):
    r = client.post("/collections", headers=as_ana, json={"name": "a\\u0000b"})
    assert r.status_code < 400


def test_nul_in_query_is_422(client, as_ana):
    assert client.get("/search", headers=as_ana, params={"q": "a\u0000b"}).status_code == 422


def test_out_of_range_path_id_is_404(client, as_ana):
    assert client.post(f"/admin/jobs/{BIG}/retry", headers=as_ana).status_code == 404
    assert client.get(f"/documents/{BIG}", headers=as_ana).status_code == 404


def test_out_of_range_query_int_is_422(client, as_ana):
    assert client.get("/me/notifications", headers=as_ana, params={"before_id": BIG}).status_code == 422
