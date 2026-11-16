import hashlib
import io
import json
import uuid

import pytest
from api_seed import seed
from conftest import bearer
from sqlalchemy import select

from roracle.models import AuditLog, Blob, Evidence, EvidenceReproduction, Job
from roracle.notes import evidence as ev
from roracle.queue import NonRetryable

pytestmark = pytest.mark.usefixtures("_blob_store_override")
NF = {"detail": "not found"}
PROV = {
    "code": {"repo": "https://git.example.org/x.git", "commit": "abc1234"},
    "environment": {"image_digest": "sha256:" + "a" * 64},
    "inputs": [{"name": "in.csv", "sha256": "b" * 64}],
    "outputs": [{"name": "out.csv"}],
    "parameters": {"lr": 0.1},
    "seed": 42,
    "agent": {"client": "claude-code", "model": "opus"},
    "started_at": "2026-01-01T00:00:00Z",
    "ended_at": "2026-01-01T01:00:00Z",
}


@pytest.fixture
def admin(make_token):
    return bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))


@pytest.fixture
def note(client, as_ana, ana, db_session):
    d, _ = seed(db_session, ana, visibility="everyone")
    db_session.commit()
    r = client.post(f"/documents/{d.id}/annotations", json={"body": "hi", "kind": "finding"}, headers=as_ana)
    return r.json()["id"]


def post(client, h, nid, prov=None, stance="supports", data=b"results", **extra):
    meta = {**(PROV if prov is None else prov), "stance": stance, **extra}
    files = {"file": ("r es/../out.ipynb", data, "text/html")} if data is not None else None
    return client.post(
        f"/annotations/{nid}/evidence", headers=h, data={"meta": json.dumps(meta)}, files=files
    )


def test_crate_shape():
    c = ev.crate(ev.Provenance(**PROV))
    assert c["@context"] == "https://w3id.org/ro/crate/1.1/context"
    types = {e["@type"] for e in c["@graph"]}
    assert {"CreativeWork", "Dataset", "CreateAction", "SoftwareSourceCode"} <= types


def test_upload_creates_pending_blob_job(client, as_ana, ana, note, db_session, s3):
    r = post(client, as_ana, note)
    assert r.status_code == 201, r.text
    j = r.json()
    sha = hashlib.sha256(b"results").hexdigest()
    assert j["file"] == {"filename": "out.ipynb", "size": 7, "sha256": sha, "scan_status": "pending"}
    assert j["stance"] == "supports" and j["uri"] is None and j["created_by"] == str(ana)
    assert j["reproductions"] == {"reproduced": 0, "failed": 0, "mine": None}
    assert j["crate"]["@context"].startswith("https://w3id.org/ro/crate")
    assert s3.exists(f"blobs/{sha[:2]}/{sha}")
    assert db_session.get(Blob, sha).refcount == 1
    job = db_session.scalar(select(Job).where(Job.kind == "evidence_scan"))
    assert job.payload == {"evidence_id": j["id"]}
    n = client.get(f"/annotations/{note}", headers=as_ana).json()
    assert n["evidence"]["supports"] == 1


def test_uri_only_has_status_none(client, as_ana, note):
    r = post(client, as_ana, note, data=None, uri="https://x.org/p", uri_sha256="c" * 64)
    assert r.status_code == 201
    assert r.json()["file"] is None and r.json()["uri"] == "https://x.org/p"
    assert r.json()["crate"]


def test_file_and_uri_exclusive_422(client, as_ana, note):
    assert post(client, as_ana, note, uri="https://x.org/p").status_code == 422


@pytest.mark.parametrize("drop", ["code", "environment", "agent", "started_at", "ended_at"])
def test_missing_provenance_422(client, as_ana, note, drop):
    assert post(client, as_ana, note, prov={k: v for k, v in PROV.items() if k != drop}).status_code == 422


def test_bad_meta_422(client, as_ana, note):
    r = client.post(f"/annotations/{note}/evidence", headers=as_ana, data={"meta": "{nope"})
    assert r.status_code == 422
    r = client.post(f"/annotations/{note}/evidence", headers=as_ana, data={"meta": " " * 300_000})
    assert r.status_code == 422
    assert post(client, as_ana, note, prov={**PROV, "ended_at": "2025-01-01T00:00:00Z"}).status_code == 422
    assert post(client, as_ana, note, prov={**PROV, "parameters": {"k": "x" * 70_000}}).status_code == 422
    assert post(client, as_ana, note, stance="maybe").status_code == 422


def test_non_finding_422(client, as_ana, ana, db_session):
    d, _ = seed(db_session, ana, visibility="everyone")
    db_session.commit()
    n = client.post(f"/documents/{d.id}/annotations", json={"body": "c"}, headers=as_ana).json()["id"]
    assert post(client, as_ana, n).status_code == 422


def test_oversize_413_writes_nothing(client, as_ana, note, app, settings, db_session):
    from roracle.auth import get_app_settings

    app.dependency_overrides[get_app_settings] = lambda: settings.model_copy(update={"evidence_max_bytes": 5})
    assert post(client, as_ana, note).status_code == 413
    assert db_session.scalar(select(Evidence)) is None


def test_other_reader_may_attach_to_everyone_note(client, as_bob, note):
    assert post(client, as_bob, note).status_code == 201


def _mk(db_session, note, ana, status, data=b"zip!", filename="a.zip"):
    sha = hashlib.sha256(data).hexdigest()
    if db_session.get(Blob, sha) is None:
        db_session.add(Blob(sha256=sha, size=len(data)))
    e = Evidence(annotation_id=uuid.UUID(note), creator_id=ana, stance="refutes", crate={}, blob_sha256=sha,
                 filename=filename, size=len(data), scan_status=status)  # fmt: skip
    db_session.add(e)
    db_session.commit()
    return e


def test_download_gate_and_headers(client, as_ana, ana, note, db_session, store, staged):
    from roracle.storage import stage

    s = stage(io.BytesIO(b"zip!"), 100)
    store.put(s, "application/zip")
    for st in ("pending", "infected", "failed", "none"):
        e = _mk(db_session, note, ana, st)
        r = client.get(f"/evidence/{e.id}/file", headers=as_ana)
        assert r.status_code == 409 and r.json() == {"detail": "not available"}
    for st in ("clean", "unscanned"):
        e = _mk(db_session, note, ana, st, filename='we"ird\r\nñ.html')
        r = client.get(f"/evidence/{e.id}/file", headers=as_ana)
        assert r.status_code == 200 and r.content == b"zip!"
        h = r.headers
        assert h["content-type"] == "application/octet-stream"
        assert (
            h["content-disposition"].startswith("attachment;")
            and "filename*=UTF-8''" in h["content-disposition"]
        )
        assert (
            "\r" not in h["content-disposition"]
            and '"ird' not in h["content-disposition"].split("filename=")[1].split(";")[0][1:-1]
        )
        assert h["x-content-type-options"] == "nosniff" and h["content-security-policy"] == "sandbox"


def test_unreadable_note_all_404(client, as_ana, as_bob, ana, bob, db_session):
    d, _ = seed(db_session, ana, visibility="everyone")
    db_session.commit()
    n = client.post(
        f"/documents/{d.id}/annotations",
        json={"body": "p", "kind": "finding", "visibility": "private"},
        headers=as_ana,
    ).json()["id"]
    e = _mk(db_session, n, ana, "clean")
    assert post(client, as_bob, n).status_code == 404
    for url in (f"/annotations/{n}/evidence", f"/evidence/{e.id}", f"/evidence/{e.id}/file"):
        assert client.get(url, headers=as_bob).json() == NF
    assert (
        client.put(f"/evidence/{e.id}/reproduction", json={"outcome": "failed"}, headers=as_bob).status_code
        == 404
    )
    assert client.delete(f"/evidence/{e.id}", headers=as_bob).status_code == 404


def test_reproductions_counts_and_clear(client, as_ana, as_bob, ana, bob, note, db_session):
    e = _mk(db_session, note, ana, "clean")
    u = f"/evidence/{e.id}/reproduction"
    assert client.put(u, json={"outcome": "reproduced", "note": "ok"}, headers=as_bob).json()["reproductions"] == {
        "reproduced": 1, "failed": 0, "mine": "reproduced"}  # fmt: skip
    assert client.put(u, json={"outcome": "failed"}, headers=as_bob).json()["reproductions"]["failed"] == 1
    client.put(u, json={"outcome": "reproduced"}, headers=as_ana)
    j = client.get(f"/evidence/{e.id}", headers=as_ana).json()
    assert j["reproductions"] == {"reproduced": 1, "failed": 1, "mine": "reproduced"}
    assert client.put(u, json={"outcome": None}, headers=as_bob).json()["reproductions"]["failed"] == 0
    assert client.put(u, json={"outcome": "x"}, headers=as_bob).status_code == 422
    assert client.put(u, json={"outcome": "failed", "note": "n" * 2001}, headers=as_bob).status_code == 422
    assert db_session.scalar(select(EvidenceReproduction).where(EvidenceReproduction.user_id == bob)) is None


def test_list_includes_refuting(client, as_ana, ana, note, db_session):
    _mk(db_session, note, ana, "clean")
    items = client.get(f"/annotations/{note}/evidence", headers=as_ana).json()["items"]
    assert [i["stance"] for i in items] == ["refutes"]
    assert client.get(f"/annotations/{note}", headers=as_ana).json()["evidence"]["refutes"] == 1


def test_delete_rules(client, as_ana, as_bob, admin, ana, bob, note, db_session):
    e = _mk(db_session, note, ana, "clean")
    assert client.delete(f"/evidence/{e.id}", headers=as_bob).status_code == 403
    assert client.delete(f"/evidence/{e.id}", headers=as_ana).status_code == 204
    assert db_session.scalar(select(AuditLog)) is None
    e2 = _mk(db_session, note, ana, "clean")
    assert client.delete(f"/evidence/{e2.id}", headers=admin).status_code == 204
    assert db_session.scalar(select(AuditLog.action)) == "evidence.delete"
    assert client.get(f"/evidence/{e2.id}", headers=as_ana).status_code == 404


# --- scan handler (fake scan) ---
@pytest.fixture
def scan_env(db_session, ana, note, store, settings):
    e = _mk(db_session, note, ana, "pending")
    return e, settings.model_copy(update={"clamav_host": "clam"}), store


def run(env, session):
    e, s, store = env
    ev.handle_evidence_scan(session, {"evidence_id": str(e.id)}, settings=s, store=store)


def test_scan_clean_infected(scan_env, db_session, monkeypatch, staged, store):
    from roracle.storage import stage

    store.put(stage(io.BytesIO(b"zip!"), 100), "x")
    monkeypatch.setattr(ev, "scan", lambda stream, host: None)
    run(scan_env, db_session)
    assert scan_env[0].scan_status == "clean"
    monkeypatch.setattr(ev, "scan", lambda stream, host: "Eicar")
    run(scan_env, db_session)
    assert scan_env[0].scan_status == "infected"


def test_scan_unscanned_without_clamav(scan_env, db_session):
    e, s, store = scan_env
    ev.handle_evidence_scan(
        db_session, {"evidence_id": str(e.id)}, settings=s.model_copy(update={"clamav_host": ""}), store=store
    )
    assert e.scan_status == "unscanned"


def test_scan_size_limit_failed_and_conn_error_propagates(scan_env, db_session, monkeypatch):
    def boom(stream, host):
        raise NonRetryable("too big")

    monkeypatch.setattr(ev, "scan", boom)
    run(scan_env, db_session)
    assert scan_env[0].scan_status == "failed"

    def down(stream, host):
        raise ConnectionError("x")

    monkeypatch.setattr(ev, "scan", down)
    with pytest.raises(ConnectionError):
        run(scan_env, db_session)


def test_scan_missing_row_noop(db_session, store, settings):
    ev.handle_evidence_scan(db_session, {"evidence_id": str(uuid.uuid4())}, settings=settings, store=store)


@pytest.mark.parametrize(
    "prov",
    [
        {"parameters": {"a": {"@id": "x"}}},
        {"parameters": {"a": [{"b": {"@type": "x"}}]}},
        {"code": {"repo": "javascript:alert(1)", "commit": "abc1234"}},
        {"code": {"repo": "file:///etc/passwd", "commit": "abc1234"}},
        {"inputs": [{"name": "i", "uri": "ftp://x"}]},
        {"uri": "data:text/html,x", "uri_sha256": "c" * 64},
        {"uri": "https://x.org/p"},
        {"uri_sha256": "c" * 64},
    ],
)
def test_provenance_hardening_422(client, as_ana, note, prov):
    assert post(client, as_ana, note, prov={**PROV, **prov}, data=None).status_code == 422


@pytest.mark.parametrize(
    "repo", ["git@github.com:org/x.git", "HTTPS://x.org/r", "ssh://h/r", "s3://b/k", "doi:10.1/x"]
)
def test_repo_schemes_ok(client, as_ana, note, repo):
    p = {**PROV, "code": {"repo": repo, "commit": "abc1234"}}
    assert post(client, as_ana, note, prov=p, data=None).status_code == 201


def test_pat_cannot_write_evidence(client, as_ana, ana, note, db_session):
    tok = bearer(
        client.post("/me/tokens", json={"name": "c", "scopes": ["read", "write"]}, headers=as_ana).json()[
            "token"
        ]
    )
    e = _mk(db_session, note, ana, "clean")
    for r in (
        post(client, tok, note),
        client.put(f"/evidence/{e.id}/reproduction", json={"outcome": "failed"}, headers=tok),
        client.delete(f"/evidence/{e.id}", headers=tok),
    ):
        assert r.status_code == 403


def test_scan_missing_object_failed_and_body_closed(scan_env, db_session, monkeypatch):
    from botocore.exceptions import ClientError

    e, s, _ = scan_env

    class Store:
        def open(self, sha):
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    ev.handle_evidence_scan(db_session, {"evidence_id": str(e.id)}, settings=s, store=Store())
    assert e.scan_status == "failed"

    closed = []

    class Body(io.BytesIO):
        def close(self):
            closed.append(1)
            super().close()

    class Store2:
        def open(self, sha):
            return Body(b"x")

    monkeypatch.setattr(ev, "scan", lambda stream, host: None)
    ev.handle_evidence_scan(db_session, {"evidence_id": str(e.id)}, settings=s, store=Store2())
    assert e.scan_status == "clean" and closed
