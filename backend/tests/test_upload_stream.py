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
    assert fake.calls.count("copy") == 2 and len(fake.objects) == 1  # ADR-0008: always copy, dedup in DB only
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


def _raw(client, headers, body, boundary="B"):
    h = {**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"}
    return client.post("/documents", headers=h, content=body)


FILE_PART = b'--B\r\nContent-Disposition: form-data; name="file"; filename="a"\r\n\r\nabc\r\n'


def test_part_header_bytes_capped(client, as_ana, fake):
    body = FILE_PART.replace(b"\r\n\r\n", b"\r\nX-Big: " + b"a" * 9000 + b"\r\n\r\n", 1) + b"--B--\r\n"
    r = _raw(client, as_ana, body)
    assert r.status_code == 400 and r.json() == {"detail": "form part header too large"}
    assert fake.objects == {}


def test_part_header_count_capped(client, as_ana, fake):
    extra = b"".join(b"X-%d: v\r\n" % i for i in range(17))
    body = FILE_PART.replace(b"\r\n\r\n", b"\r\n" + extra + b"\r\n", 1) + b"--B--\r\n"
    r = _raw(client, as_ana, body)
    assert r.status_code == 400 and r.json() == {"detail": "form part header too large"}


def test_part_count_capped(client, as_ana, fake):
    body = b"".join(b'--B\r\nContent-Disposition: form-data; name="f%d"\r\n\r\nv\r\n' % i for i in range(101))
    r = _raw(client, as_ana, body + b"--B--\r\n")
    assert r.status_code == 422 and r.json() == {"detail": "too many form parts"}


def test_total_body_capped(client, as_ana, fake, settings_small_limit):
    body = FILE_PART + b"--B--\r\n" + b"x" * (3 * 1024 * 1024 + 1)  # 1 MiB limit + 2 MiB allowance
    r = _raw(client, as_ana, body)
    assert r.status_code == 413 and r.json() == {"detail": "request body too large"}
    assert fake.objects == {}


def _staged(store, sha="ab" + "0" * 62):
    from roracle.storage import StagedBlob

    up = TmpUpload(store, "x", 100)
    store.client.objects[up.key] = b"data"
    return StagedBlob(up, sha, 4, "f", "x")


@pytest.mark.parametrize("exists", [True, False])
def test_promote_always_copies(exists):
    f = FakeS3()
    st = BlobStore(f, "b")
    sha = "ab" + "0" * 62
    if exists:
        f.objects[st.key(sha)] = b"data"
    staged = _staged(st, sha)
    st.promote(staged, "x")
    assert f.calls == ["copy"] and "head_object" not in f.calls  # ADR-0008: no existence timing oracle
    assert staged.upload.key in f.objects  # promote does not own tmp cleanup; the route does


def test_duplicate_409_and_quota_413_leave_no_tmp(client, as_ana, fake, settings, app):
    from roracle.auth import get_app_settings

    assert post(client, as_ana, b"same").status_code == 201
    assert post(client, as_ana, b"same").status_code == 409
    assert not [k for k in fake.objects if k.startswith("tmp/")]
    app.dependency_overrides[get_app_settings] = lambda: settings.model_copy(update={"upload_quota_bytes": 1})
    assert post(client, as_ana, b"other-bytes").status_code == 413
    assert not [k for k in fake.objects if k.startswith("tmp/")]
    assert fake.calls.count("delete_object") == 3  # one abort per upload: single owner (route)


def test_empty_filename_falls_back_to_upload(client, as_ana, fake, db_session):
    r = _raw(client, as_ana, FILE_PART.replace(b'filename="a"', b'filename=""') + b"--B--\r\n")
    assert r.status_code == 201
    from sqlalchemy import select

    from roracle.models import DocumentVersion

    assert db_session.scalar(select(DocumentVersion.filename)) == "upload"


def test_client_disconnect_aborts_tmp():
    from starlette.requests import ClientDisconnect

    from roracle.upload_stream import receive

    f = FakeS3()

    class Req:
        headers = {"content-type": "multipart/form-data; boundary=B"}  # noqa: RUF012

        async def stream(self):
            yield FILE_PART + b"more"
            raise ClientDisconnect

    with pytest.raises(ClientDisconnect):
        asyncio.run(receive(Req(), BlobStore(f, "b"), 1000))
    assert "delete_object" in f.calls and f.objects == {}


def test_request_session_committed_before_body_read(client, as_ana, fake, db_session, monkeypatch):
    """Real JWT user upsert has run; its row lock must be released before the body streams."""
    from roracle.models import User
    from roracle.upload_stream import Received

    seen = []

    async def rcv(request, store, max_bytes, **kw):
        seen.append(db_session.in_transaction())
        return Received({}, None)

    monkeypatch.setattr("roracle.api.routes.documents.receive", rcv)
    client.post("/documents", headers=as_ana, files={"file": ("a", b"1")})
    assert seen == [False] and db_session.query(User).count() == 1
