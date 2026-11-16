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


def test_json_over_cap_is_413(client, as_ana):
    big = '{"name": "' + "a" * (5 * 1024 * 1024) + '"}'
    r = client.post("/collections", headers={**as_ana, "content-type": "application/json"}, content=big)
    assert r.status_code == 413


def test_nul_split_across_chunks_is_422(client, as_ana):
    def gen():
        yield b'{"name": "a\\u00'
        yield b'00b"}'

    r = client.post("/collections", headers={**as_ana, "content-type": "application/json"}, content=gen())
    assert r.status_code == 422


def test_multipart_is_not_scanned(client, as_ana):
    big = b"x" * (4 * 1024 * 1024)
    r = client.post("/documents", headers=as_ana, files={"file": ("a.pdf", big)})
    assert r.status_code != 413


def test_missing_content_type_still_capped(client, as_ana):
    r = client.post("/collections", headers=as_ana, content=b'{"name": "' + b"a" * (5 * 1024 * 1024) + b'"}')
    assert r.status_code == 413


def test_merge_patch_json_nul_is_422(client, as_ana):
    h = {**as_ana, "content-type": "application/merge-patch+json"}
    assert client.post("/collections", headers=h, content=b'{"name": "a\\u0000b"}').status_code == 422


def test_streaming_response_receive_does_not_recurse(client, as_ana):
    # a GET with no body: later receive() calls (disconnect polling) must reach the real receive
    assert (
        client.get("/documents/00000000-0000-4000-8000-000000000000/file", headers=as_ana).status_code == 404
    )


def test_json_cap_follows_draft_max_bytes():
    from roracle.api.input_guard import JSON_MAX, json_cap

    assert json_cap(1024 * 1024) == JSON_MAX  # floor
    big = 8 * 1024 * 1024
    assert json_cap(big) >= 2 * big  # JSON-escaped non-ASCII is up to 2x the UTF-8 size
