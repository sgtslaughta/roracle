import json
import uuid

import pytest
from api_seed import seed, user_id

from roracle.api.routes.library import get_es
from roracle.models import Tag

pytestmark = pytest.mark.usefixtures("_blob_store_override")


class FakeES:
    def __init__(self, hits=()):
        self.hits, self.calls = list(hits), []

    def search(self, **kw):
        self.calls.append(kw)
        return {"hits": {"hits": self.hits}}


@pytest.fixture
def fes(app):
    f = FakeES()
    app.dependency_overrides[get_es] = lambda: f
    return f


@pytest.fixture
def ana(db_session, client, as_ana):
    return user_id(db_session, client, as_ana, "ana")


@pytest.fixture
def bob(db_session, client, as_bob):
    return user_id(db_session, client, as_bob, "bob")


def put(store, v, data):
    store.put_bytes(store.key(v.blob_sha256), data, "application/pdf")


def paths(d, n=1):
    return [
        f"/documents/{d.id}",
        f"/documents/{d.id}/versions/{n}/file",
        f"/documents/{d.id}/versions/{n}/docling",
    ]


def test_detail(client, as_ana, ana, db_session):
    d, _ = seed(db_session, ana, title="T", versions=2, venue="ICML")
    db_session.add(Tag(document_id=d.id, tag="ml"))
    db_session.flush()
    r = client.get(f"/documents/{d.id}", headers=as_ana)
    assert r.status_code == 200
    b = r.json()
    assert b["id"] == str(d.id) and b["title"] == "T" and b["owner"] == {"id": str(ana), "username": "ana"}
    assert b["stage"] == "searchable" and b["file_type"] == "pdf" and b["page_count"] == 3
    assert b["summary"] == "sum" and b["visibility"] == "everyone" and b["org_unit"] == "/"
    assert b["venue"] == "ICML" and b["flag"] is None and b["tags"] == ["ml"] and b["year"] == 2020
    assert b["votes"] == {"up": 0, "down": 0, "mine": 0}
    assert [(x["version"], x["is_latest"], x["filename"]) for x in b["versions"]] == [
        (2, True, "p.pdf"),
        (1, False, "p.pdf"),
    ]
    assert {"id", "uploaded_at"} <= set(b["versions"][0])


def test_invisible_404_on_every_endpoint(client, as_ana, bob, db_session, store, fes):
    d, v = seed(db_session, bob, visibility="private")
    put(store, v, b"x")
    for p in paths(d):
        r = client.get(p, headers=as_ana)
        assert (r.status_code, r.json()) == (404, {"detail": "not found"}), p
    r = client.get(f"/documents/{uuid.uuid4()}", headers=as_ana)
    assert (r.status_code, r.json()) == (404, {"detail": "not found"})


def test_missing_version_404(client, as_ana, ana, db_session, fes):
    d, _ = seed(db_session, ana)
    for n in (2, 0):
        assert client.get(f"/documents/{d.id}/versions/{n}/file", headers=as_ana).status_code == 404
        assert client.get(f"/documents/{d.id}/versions/{n}/docling", headers=as_ana).status_code == 404


def test_file_download(client, as_ana, ana, db_session, store):
    d, v = seed(db_session, ana, filename="paper.pdf")
    put(store, v, b"%PDF-bytes")
    r = client.get(f"/documents/{d.id}/versions/1/file", headers=as_ana)
    assert r.status_code == 200 and r.content == b"%PDF-bytes"
    assert r.headers["content-type"] == "application/pdf"
    assert (
        r.headers["content-disposition"] == "attachment; filename=\"paper.pdf\"; filename*=UTF-8''paper.pdf"
    )
    assert r.headers["x-content-type-options"] == "nosniff"


def test_file_unicode_filename(client, as_ana, ana, db_session, store):
    d, v = seed(db_session, ana, filename='Über "Größe" é.pdf')
    put(store, v, b"x")
    cd = client.get(f"/documents/{d.id}/versions/1/file", headers=as_ana).headers["content-disposition"]
    assert (
        cd
        == "attachment; filename=\"ber Gre .pdf\"; filename*=UTF-8''%C3%9Cber%20%22Gr%C3%B6%C3%9Fe%22%20%C3%A9.pdf"
    )


def test_file_inline(client, as_ana, ana, db_session, store):
    d, v = seed(db_session, ana)
    put(store, v, b"x")
    cd = client.get(f"/documents/{d.id}/versions/1/file?inline=1", headers=as_ana).headers[
        "content-disposition"
    ]
    assert cd.startswith("inline; filename=")


def test_inline_html_never_inline(client, as_ana, ana, db_session, store):
    d, v = seed(db_session, ana, filename="x.html", content_type="text/html")
    put(store, v, b"<script>1</script>")
    r = client.get(f"/documents/{d.id}/versions/1/file?inline=1", headers=as_ana)
    assert r.headers["content-disposition"].startswith("attachment;")


def test_content_type_falls_back_to_extension(client, as_ana, ana, db_session, store):
    d, v = seed(db_session, ana, filename="a.pdf", content_type="")
    put(store, v, b"x")
    assert (
        client.get(f"/documents/{d.id}/versions/1/file", headers=as_ana).headers["content-type"]
        == "application/pdf"
    )


@pytest.mark.parametrize("stage", ["rejected", "cancelled"])
def test_rejected_versions_404(client, as_ana, ana, db_session, store, fes, stage):
    d, v = seed(db_session, ana, stage=stage)
    put(store, v, b"x")
    assert client.get(f"/documents/{d.id}/versions/1/file", headers=as_ana).status_code == 404
    assert client.get(f"/documents/{d.id}/versions/1/docling", headers=as_ana).status_code == 404


def test_docling_compact(client, as_ana, ana, db_session, store, fes):
    d, v = seed(db_session, ana)
    store.put_bytes(
        f"derived/{v.id}/docling.json",
        json.dumps(
            {"pages": {"1": {"size": {"width": 612.0, "height": 792.0}, "page_no": 1}}, "texts": ["big"]}
        ).encode(),
        "application/json",
    )
    bb = {"page": 1, "l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "BOTTOMLEFT"}
    src = lambda n, bbs: {"_source": {"chunk_id": f"{v.id}:{n}", "bboxes": bbs, "pages": [1]}}
    fes.hits = [src(10, [bb]), src(2, [bb, {**bb, "page": 2}]), src(3, [])]  # no bbox: skipped; sorted by n
    r = client.get(f"/documents/{d.id}/versions/1/docling", headers=as_ana)
    assert r.status_code == 200
    assert r.json() == {
        "pages": {"1": {"size": {"width": 612.0, "height": 792.0}}},
        "chunks": [
            {"chunk_id": f"{v.id}:2", "page_no": 1, "bbox": {"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "BOTTOMLEFT"}},
            {"chunk_id": f"{v.id}:10", "page_no": 1, "bbox": {"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "BOTTOMLEFT"}},
        ],
    }  # fmt: skip


def test_docling_not_parsed_404(client, as_ana, ana, db_session, fes):
    d, _ = seed(db_session, ana, stage="scanned")
    assert client.get(f"/documents/{d.id}/versions/1/docling", headers=as_ana).status_code == 404
