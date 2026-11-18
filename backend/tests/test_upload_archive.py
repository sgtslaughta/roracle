import pytest
from archive_helpers import make_tar, make_zip, zip_symlink
from sqlalchemy import func, select

from roracle.auth import get_app_settings
from roracle.models import Document, DocumentVersion

pytestmark = pytest.mark.usefixtures("_blob_store_override")
DOC = rb"\documentclass{article}\begin{document}x\end{document}"


def up(client, headers, data: bytes, name="p.zip", **form):
    return client.post(
        "/documents", headers=headers, data=form, files={"file": (name, data, "application/zip")}
    )


def count(db_session, model):
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model))


def test_three_file_zip_creates_three_documents(client, as_ana, db_session):
    r = up(client, as_ana, make_zip({"a.txt": b"aaa", "d/b.md": b"# bbb", "c.csv": b"1,2"}), title="ignored")
    assert r.status_code == 201
    body = r.json()
    assert body["archive"] is True and body["skipped"] == []
    assert [d["filename"] for d in body["documents"]] == ["a.txt", "b.md", "c.csv"]
    assert all({"document_id", "version_id", "sha256"} <= set(d) for d in body["documents"])
    assert count(db_session, Document) == 3
    names = set(db_session.scalars(select(DocumentVersion.filename)))
    assert names == {"a.txt", "b.md", "c.csv"}
    assert all(t is None for t in db_session.scalars(select(Document.title)))  # title is single-file only


def test_form_fields_apply_to_each_member(client, as_ana, db_session):
    up(client, as_ana, make_zip({"a.txt": b"1", "b.txt": b"2"}), visibility="private")
    assert set(db_session.scalars(select(Document.visibility))) == {"private"}


def test_tar_gz_upload_expands(client, as_ana, db_session):
    r = up(client, as_ana, make_tar({"a.txt": b"A", "b.txt": b"B"}, gz=True), name="p.tar.gz")
    assert len(r.json()["documents"]) == 2


def test_unsafe_and_nested_members_skipped_with_reasons(client, as_ana, db_session):
    z = make_zip(
        {"ok.txt": b"ok", "../evil": b"x", "in.zip": make_zip({"x": b"y"})}, extra=[zip_symlink("ln")]
    )
    body = up(client, as_ana, z).json()
    assert [d["filename"] for d in body["documents"]] == ["ok.txt"]
    reasons = {s["name"]: s["reason"] for s in body["skipped"]}
    assert "unsafe path" in reasons["../evil"]
    assert "nested archive" in reasons["in.zip"]
    assert "not a regular file" in reasons["ln"]
    assert count(db_session, Document) == 1


def test_member_cap_skips_the_rest(client, as_ana, db_session, monkeypatch):
    monkeypatch.setattr("roracle.ingest.archive.MAX_MEMBERS", 2)
    body = up(client, as_ana, make_zip({f"f{i}.txt": bytes([65 + i]) for i in range(4)})).json()
    assert len(body["documents"]) == 2
    assert any("rest are skipped" in s["reason"] for s in body["skipped"])


def test_quota_exceeded_mid_archive_keeps_earlier_members(client, as_ana, db_session, app, settings):
    app.dependency_overrides[get_app_settings] = lambda: settings.model_copy(update={"upload_quota_bytes": 9})
    body = up(client, as_ana, make_zip({"a.txt": b"1234", "b.txt": b"5678", "c.txt": b"9999"})).json()
    assert [d["filename"] for d in body["documents"]] == ["a.txt", "b.txt"]
    assert body["skipped"] == [{"name": "c.txt", "reason": "upload quota exceeded"}]
    assert count(db_session, Document) == 2


def test_duplicate_member_skipped(client, as_ana, db_session):
    client.post("/documents", headers=as_ana, files={"file": ("a.txt", b"same", "text/plain")})
    body = up(client, as_ana, make_zip({"x.txt": b"same", "y.txt": b"new"})).json()
    assert [d["filename"] for d in body["documents"]] == ["y.txt"]
    assert body["skipped"][0]["name"] == "x.txt" and "already in the library" in body["skipped"][0]["reason"]


def test_latex_source_zip_stays_one_document(client, as_ana, db_session):
    r = up(client, as_ana, make_zip({"main.tex": DOC, "fig.png": b"png"}), name="paper.zip")
    assert r.status_code == 201 and set(r.json()) == {"document_id", "version_id", "sha256"}
    assert count(db_session, Document) == 1


def test_non_archive_response_unchanged(client, as_ana):
    r = up(client, as_ana, b"%PDF-1", name="p.pdf")
    assert set(r.json()) == {"document_id", "version_id", "sha256"}


def test_corrupt_archive_is_422(client, as_ana, db_session):
    r = up(client, as_ana, b"not a zip", name="p.zip")
    assert r.status_code == 422 and "archive" in r.json()["detail"]
    assert count(db_session, Document) == 0
