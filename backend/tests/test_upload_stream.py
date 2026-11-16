import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from roracle.api.routes.documents import get_blob_store
from roracle.models import Blob, Document
from roracle.storage import PART, BlobStore, EmptyFile, TmpUpload, TooLarge

pytestmark = pytest.mark.usefixtures("_blob_store_override")


class FakeS3:
    """In-memory S3 recording multipart calls."""

    exceptions = SimpleNamespace(ClientError=ClientError)

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.parts: dict[str, list[bytes]] = {}
        self.on_part = lambda: None

    def _rec(self, name, **kw):
        self.calls.append(name)

    def put_object(self, Bucket, Key, Body, **kw):
        self._rec("put_object")
        self.objects[Key] = Body

    def create_multipart_upload(self, Bucket, Key, **kw):
        self._rec("create_multipart_upload")
        self.parts[Key] = []
        return {"UploadId": "u1"}

    def upload_part(self, Bucket, Key, UploadId, PartNumber, Body):
        self._rec("upload_part")
        self.on_part()
        self.parts[Key].append(Body)
        return {"ETag": f"e{PartNumber}"}

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        self._rec("complete_multipart_upload")
        assert [p["PartNumber"] for p in MultipartUpload["Parts"]] == list(range(1, len(self.parts[Key]) + 1))
        self.objects[Key] = b"".join(self.parts.pop(Key))

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self._rec("abort_multipart_upload")
        self.parts.pop(Key, None)

    def delete_object(self, Bucket, Key):
        self._rec("delete_object")
        self.objects.pop(Key, None)

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    def copy(self, src, Bucket, Key, ExtraArgs=None):
        self._rec("copy")
        self.objects[Key] = self.objects[src["Key"]]


@pytest.fixture
def fake(app):
    f = FakeS3()
    st = BlobStore(f, "b")
    app.dependency_overrides[get_blob_store] = lambda: st
    return f


def post(client, headers, data=b"%PDF-1", **form):
    return client.post(
        "/documents", headers=headers, data=form, files={"file": ("p.pdf", data, "application/pdf")}
    )


def test_small_upload_is_single_put_then_copy_and_tmp_deleted(client, as_ana, fake):
    r = post(client, as_ana, b"hello")
    sha = hashlib.sha256(b"hello").hexdigest()
    assert r.status_code == 201 and r.json()["sha256"] == sha
    assert fake.calls[0] == "put_object" and "create_multipart_upload" not in fake.calls
    assert list(fake.objects) == [f"blobs/{sha[:2]}/{sha}"]


def test_large_upload_uses_multipart_parts(client, as_ana, fake):
    data = b"a" * (PART * 2 + 5)
    r = post(client, as_ana, data)
    assert r.status_code == 201
    assert fake.calls.count("upload_part") == 3 and "complete_multipart_upload" in fake.calls
    assert list(fake.objects.values()) == [data]


def test_oversize_aborts_multipart_and_is_413(client, as_ana, fake, settings_small_limit, db_session):
    from sqlalchemy import func, select

    # 1 MiB limit < PART, so use the TmpUpload cap directly for the multipart abort case below
    r = post(client, as_ana, b"x" * (1024 * 1024 + 1))
    assert r.status_code == 413 and r.json() == {"detail": "file exceeds 1 MB limit"}
    assert fake.objects == {} and db_session.scalar(select(func.count()).select_from(Document)) == 0


def test_oversize_after_parts_sent_aborts_mpu():
    f = FakeS3()
    up = TmpUpload(BlobStore(f, "b"), "x", PART + 10)
    up.feed(b"a" * PART)
    up.flush()
    with pytest.raises(TooLarge):
        up.feed(b"a" * 11)
    up.abort()
    assert "abort_multipart_upload" in f.calls and f.objects == {} and f.parts == {}


def test_empty_is_400_and_leaves_nothing(client, as_ana, fake):
    r = post(client, as_ana, b"")
    assert r.status_code == 400 and r.json() == {"detail": "empty file"}
    assert fake.objects == {}
    with pytest.raises(EmptyFile):
        TmpUpload(BlobStore(fake, "b"), "x", 10).finish()


def test_duplicate_content_creates_no_second_blob_object(client, as_ana, as_bob, fake, db_session):
    from sqlalchemy import select

    assert post(client, as_ana, b"same", visibility="private").status_code == 201
    assert post(client, as_bob, b"same").status_code == 201
    assert fake.calls.count("copy") == 1 and len(fake.objects) == 1
    assert db_session.scalar(select(Blob.refcount)) == 2


def test_fields_after_file_and_validation(client, as_ana, fake):
    boundary = "BND"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n'
        f"Content-Type: text/plain\r\n\r\nabc\r\n"
        f'--{boundary}\r\nContent-Disposition: form-data; name="visibility"\r\n\r\nprivate\r\n--{boundary}--\r\n'
    ).encode()
    h = {**as_ana, "Content-Type": f"multipart/form-data; boundary={boundary}"}
    assert client.post("/documents", headers=h, content=body).status_code == 201
    bad = body.replace(b"private", b"bogus")
    r = client.post("/documents", headers=h, content=bad)
    assert r.status_code == 422 and fake.calls.count("delete_object") >= 1
    assert list(fake.objects) and all(k.startswith("blobs/") for k in fake.objects)  # no tmp left


def test_missing_file_oversized_field_and_wrong_type_are_422(client, as_ana, fake):
    assert client.post("/documents", headers=as_ana, data={"title": "t"}).status_code == 422
    assert post(client, as_ana, title="x" * 600).status_code == 422
    big = {"title": "x" * 70_000}
    assert post(client, as_ana, **big).status_code == 422
    r = client.post("/documents", headers={**as_ana, "Content-Type": "application/json"}, content=b"{}")
    assert r.status_code == 422
    assert fake.objects == {}


def test_second_file_part_rejected(client, as_ana, fake):
    r = client.post("/documents", headers=as_ana, files=[("file", ("a", b"1")), ("file", ("b", b"2"))])
    assert r.status_code == 422 and fake.objects == {}


def test_unauthenticated_upload_reads_no_body(client, fake):
    assert client.post("/documents", files={"file": ("a", b"1")}).status_code == 401
    assert fake.calls == []


def test_openapi_documents_multipart_fields(app):
    schema = app.openapi()["paths"]["/documents"]["post"]["requestBody"]["content"]["multipart/form-data"][
        "schema"
    ]
    assert set(schema["properties"]) == {"file", "title", "visibility", "force"}
    ev = app.openapi()["paths"]["/annotations/{note_id}/evidence"]["post"]["requestBody"]
    assert set(ev["content"]["multipart/form-data"]["schema"]["properties"]) == {"meta", "file"}


def test_body_streams_through_middleware_without_buffering(app, as_ana, fake):
    """Parts reach S3 while the client is still sending: nothing (incl. the metrics middleware) buffers the body."""
    chunk, total = b"z" * 65536, 24 * 1024 * 1024 // 65536
    sent = 0
    seen_at_first_part: list[int] = []
    fake.on_part = lambda: seen_at_first_part.append(sent)
    b = b'--B\r\nContent-Disposition: form-data; name="file"; filename="f"\r\n\r\n'
    tail = b"\r\n--B--\r\n"
    msgs = [b] + [chunk] * total + [tail]

    async def run():
        nonlocal sent
        it = iter(msgs)

        async def receive():
            nonlocal sent
            await asyncio.sleep(0)
            try:
                body = next(it)
            except StopIteration:
                return {"type": "http.disconnect"}
            sent += 1
            return {"type": "http.request", "body": body, "more_body": body is not tail}

        out = []

        async def send(m):
            out.append(m)

        hdrs = [
            (k.lower().encode(), v.encode())
            for k, v in {**as_ana, "content-type": "multipart/form-data; boundary=B"}.items()
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "path": "/documents",
            "raw_path": b"/documents",
            "query_string": b"",
            "headers": hdrs,
            "scheme": "http",
            "server": ("t", 80),
            "client": ("c", 1),
            "root_path": "",
        }
        await app(scope, receive, send)
        return out

    out = asyncio.run(run())
    assert out[0]["status"] == 201, out
    assert seen_at_first_part and seen_at_first_part[0] < len(msgs) // 2  # first 8 MiB part flushed early
    assert json.loads(out[1]["body"])["sha256"]
