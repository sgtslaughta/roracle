from sqlalchemy import func, select

from roracle.models import Blob, Document, DocumentVersion, Job


def _up(client, headers, data=b"%PDF-1", name="paper.pdf", **form):
    return client.post(
        "/documents", headers=headers, data=form, files={"file": (name, data, "application/pdf")}
    )


def test_upload_creates_document_version_blob_and_job(client, as_ana, s3, db_session):
    r = _up(client, as_ana)
    assert r.status_code == 201
    body = r.json()
    job = db_session.scalar(select(Job))
    assert job.kind == "ingest" and job.payload == {"version_id": body["version_id"]}
    assert s3.exists(f"blobs/{body['sha256'][:2]}/{body['sha256']}")
    doc = db_session.scalar(select(Document))
    assert doc.title == "paper.pdf" and doc.visibility == "everyone"
    assert doc.org_unit == "/Mathematics/Spectral Theory"


def test_duplicate_of_visible_document_is_409_unless_forced(client, as_ana, db_session):
    first = _up(client, as_ana).json()
    r = _up(client, as_ana)
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "duplicate", "document_id": first["document_id"]}
    assert _up(client, as_ana, force="true").status_code == 201
    assert db_session.scalar(select(Blob.refcount)) == 2


def test_duplicate_of_invisible_document_does_not_leak(client, as_ana, as_bob):
    _up(client, as_ana, data=b"secret", visibility="private")
    r = _up(client, as_bob, data=b"secret")
    assert r.status_code == 201 and set(r.json()) == {"document_id", "version_id", "sha256"}


def test_same_bytes_two_users_one_blob_refcount_two(client, as_ana, as_bob, db_session):
    _up(client, as_ana, visibility="private")
    assert _up(client, as_bob).status_code == 201
    assert db_session.scalar(select(func.count()).select_from(Blob)) == 1
    assert db_session.scalar(select(Blob.refcount)) == 2


def test_empty_file_is_400(client, as_ana):
    r = _up(client, as_ana, data=b"")
    assert r.status_code == 400 and r.json() == {"detail": "empty file"}


def test_oversize_is_413_and_writes_nothing(client, as_ana, settings_small_limit, s3, db_session):
    before = s3.keys()
    r = _up(client, as_ana, data=b"x" * (1024 * 1024 + 1))
    assert r.status_code == 413
    assert r.json() == {"detail": "file exceeds 1 MB limit"}
    assert s3.keys() == before
    for m in (Document, Blob, Job):
        assert db_session.scalar(select(func.count()).select_from(m)) == 0


def test_traversal_filename_is_only_metadata(client, as_ana, db_session, s3):
    body = _up(client, as_ana, name="../../etc/passwd").json()
    v = db_session.scalar(select(DocumentVersion))
    assert v.filename == "../../etc/passwd"
    assert s3.exists(f"blobs/{body['sha256'][:2]}/{body['sha256']}")


def test_invalid_visibility_is_422(client, as_ana):
    assert _up(client, as_ana, visibility="bogus").status_code == 422


def test_unauthenticated_is_401(client):
    assert client.post("/documents", files={"file": ("a", b"x")}).status_code == 401
