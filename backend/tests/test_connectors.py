import io
import os
import uuid

from sqlalchemy import select, text

from roracle.connectors.run import ensure_tick, handle_connector_run, handle_connector_tick
from roracle.ingest.intake import add_document, add_version
from roracle.models import (
    AuditLog,
    Connector,
    ConnectorItem,
    ConnectorRun,
    Document,
    DocumentVersion,
    Job,
    Notification,
    User,
)
from roracle.storage import stage


def put(s3_client, bucket, key, data: bytes):
    s3_client.put_object(Bucket=bucket, Key=key, Body=data)


def s3_connector(db, owner, settings, bucket, prefix, **kw):
    c = Connector(
        name=kw.pop("name", "s3c"),
        kind="s3",
        owner_id=owner.id,
        visibility=kw.pop("visibility", "org_unit"),
        org_unit="/Physics",
        config={
            "endpoint": settings.s3_endpoint,
            "bucket": bucket,
            "prefix": prefix,
            "region": "us-east-1",
            "access_key": settings.s3_access_key,
            "secret_key": settings.s3_secret_key,
        },
        **kw,
    )
    db.add(c)
    db.commit()
    return c


def run(db, sf, settings, store, c):
    handle_connector_run(db, {"connector_id": str(c.id)}, settings=settings, store=store, session_factory=sf)
    db.rollback()
    return db.scalars(
        select(ConnectorRun).where(ConnectorRun.connector_id == c.id).order_by(ConnectorRun.id.desc())
    ).first()


def test_intake_add_document_and_version(db_session, user, store, staged):
    doc, v1 = add_document(
        db_session, store, staged, owner_id=user.id, org_unit="/a", visibility="private", filename="a.pdf",
        content_type="application/pdf",
    )  # fmt: skip
    s2 = stage(io.BytesIO(b"second"), 100)
    v2 = add_version(db_session, store, s2, doc, filename="a.pdf", content_type="application/pdf")
    assert (v1.version, v2.version) == (1, 2)
    assert db_session.scalar(select(Job.id).where(Job.payload["version_id"].astext == str(v2.id)))


def test_s3_run_ingests_skips_unchanged_and_versions_changes(
    db_session, session_factory, settings, store, s3_client, test_bucket, prefix, owner
):
    put(s3_client, test_bucket, prefix + "a.pdf", b"alpha")
    put(s3_client, test_bucket, prefix + "dir/b.pdf", b"bravo")
    put(s3_client, test_bucket, prefix + "dir/", b"")
    c = s3_connector(db_session, owner, settings, test_bucket, prefix)
    r = run(db_session, session_factory, settings, store, c)
    assert r.status == "ok" and r.stats["ingested"] == 2 and r.stats["seen"] == 2
    docs = db_session.scalars(select(Document)).all()
    assert {d.owner_id for d in docs} == {owner.id} and {d.visibility for d in docs} == {"org_unit"}
    assert {d.org_unit for d in docs} == {"/Physics"} and {d.connector_id for d in docs} == {c.id}
    assert db_session.scalar(select(Connector.last_run_at).where(Connector.id == c.id)) is not None
    r = run(db_session, session_factory, settings, store, c)
    assert r.stats["ingested"] == 0 and r.stats["unchanged"] == 2
    put(s3_client, test_bucket, prefix + "a.pdf", b"alpha v2")
    r = run(db_session, session_factory, settings, store, c)
    assert r.stats["versions"] == 1 and r.stats["unchanged"] == 1
    doc_id = db_session.scalar(select(ConnectorItem.document_id).where(ConnectorItem.key == prefix + "a.pdf"))
    assert sorted(
        db_session.scalars(select(DocumentVersion.version).where(DocumentVersion.document_id == doc_id))
    ) == [1, 2]
    assert db_session.scalar(select(Notification.id)) is None  # ok runs are silent


def test_duplicate_bytes_skipped_and_recorded(
    db_session, session_factory, settings, store, s3_client, test_bucket, prefix, owner
):
    put(s3_client, test_bucket, prefix + "a.pdf", b"same bytes")
    put(s3_client, test_bucket, prefix + "b.pdf", b"same bytes")
    c = s3_connector(db_session, owner, settings, test_bucket, prefix)
    r = run(db_session, session_factory, settings, store, c)
    assert r.stats["ingested"] == 1 and r.stats["duplicate"] == 1
    ids = set(db_session.scalars(select(ConnectorItem.document_id)))
    assert len(ids) == 1 and db_session.scalar(select(text("count(*)")).select_from(Document)) == 1


def test_too_large_counted_without_failing(
    db_session, session_factory, settings, store, s3_client, test_bucket, prefix, owner
):
    put(s3_client, test_bucket, prefix + "big.bin", b"x" * 50)
    s = settings.model_copy(update={"max_upload_bytes": 10})
    c = s3_connector(db_session, owner, settings, test_bucket, prefix)
    r = run(db_session, session_factory, s, store, c)
    assert r.status == "ok" and r.stats["too_large"] == 1 and r.stats["ingested"] == 0


def test_item_cap_gives_partial_and_notifies_admin(
    db_session, session_factory, settings, store, s3_client, test_bucket, prefix, owner
):
    for i in range(3):
        put(s3_client, test_bucket, f"{prefix}f{i}", f"data {i}".encode())
    s = settings.model_copy(update={"connector_max_items": 2})
    c = s3_connector(db_session, owner, settings, test_bucket, prefix)
    r = run(db_session, session_factory, s, store, c)
    assert r.status == "partial" and r.stats["ingested"] == 2 and "cap" in r.log
    n = db_session.scalar(select(Notification).where(Notification.kind == "ops.connector_run"))
    assert n.user_id == owner.id and n.payload["status"] == "partial" and n.payload["run_id"] == r.id


def test_source_error_fails_run_and_unreachable_never_raises(
    db_session, session_factory, settings, store, test_bucket, owner
):
    c = s3_connector(db_session, owner, settings, test_bucket, "x/")
    c.config = {**c.config, "bucket": "no-such-bucket-zzz"}
    db_session.commit()
    r = run(db_session, session_factory, settings, store, c)
    assert r.status == "failed" and r.log
    assert db_session.scalar(select(Notification).where(Notification.kind == "ops.connector_run")) is not None


def test_run_of_missing_connector_is_noop(db_session, session_factory, settings, store):
    handle_connector_run(
        db_session,
        {"connector_id": str(uuid.uuid4())},
        settings=settings,
        store=store,
        session_factory=session_factory,
    )
    assert db_session.scalar(select(ConnectorRun.id)) is None


def test_disabled_connector_still_runs_when_triggered(
    db_session, session_factory, settings, store, s3_client, test_bucket, prefix, owner
):
    put(s3_client, test_bucket, prefix + "a", b"disabled but manual")
    c = s3_connector(db_session, owner, settings, test_bucket, prefix, enabled=False)
    assert run(db_session, session_factory, settings, store, c).stats["ingested"] == 1


def test_tick_enqueues_once_per_due_connector_and_reschedules(db_session, settings, owner):
    due = Connector(
        name="due", kind="fs", owner_id=owner.id, visibility="private", schedule_minutes=5, config={}
    )
    manual = Connector(name="manual", kind="fs", owner_id=owner.id, visibility="private", config={})
    off = Connector(
        name="off",
        kind="fs",
        owner_id=owner.id,
        visibility="private",
        schedule_minutes=5,
        enabled=False,
        config={},
    )
    db_session.add_all([due, manual, off])
    db_session.commit()
    handle_connector_tick(db_session, {}, settings=settings)
    handle_connector_tick(db_session, {}, settings=settings)  # not due again; run job already queued
    db_session.commit()
    jobs = db_session.scalars(select(Job).where(Job.kind == "connector_run")).all()
    assert [j.payload for j in jobs] == [{"connector_id": str(due.id)}]
    assert db_session.scalar(select(Connector.next_run_at).where(Connector.id == due.id)) is not None
    assert db_session.scalar(select(Connector.next_run_at).where(Connector.id == manual.id)) is None
    assert len(db_session.scalars(select(Job).where(Job.kind == "connector_tick")).all()) == 1


def test_tick_skips_connector_with_active_run_job(db_session, settings, owner):
    c = Connector(
        name="due", kind="fs", owner_id=owner.id, visibility="private", schedule_minutes=5, config={}
    )
    db_session.add(c)
    db_session.flush()
    db_session.add(Job(kind="connector_run", payload={"connector_id": str(c.id)}, status="running"))
    db_session.commit()
    handle_connector_tick(db_session, {}, settings=settings)
    assert len(db_session.scalars(select(Job).where(Job.kind == "connector_run")).all()) == 1


def test_ensure_tick_is_idempotent(db_session, session_factory):
    ensure_tick(session_factory)
    ensure_tick(session_factory)
    assert len(db_session.scalars(select(Job).where(Job.kind == "connector_tick")).all()) == 1


# ---- routes

S3 = {
    "endpoint": "http://minio:9000",
    "bucket": "b",
    "prefix": "p/",
    "access_key": "AK",
    "secret_key": "SECRET",
}


def body(**kw):
    return {"name": "n1", "kind": "s3", "config": S3, "visibility": "everyone", **kw}


def test_non_admin_403_and_anonymous_401(client, as_ana):
    assert client.get("/admin/connectors", headers=as_ana).status_code == 403
    assert client.post("/admin/connectors", headers=as_ana, json=body()).status_code == 403
    assert client.get("/admin/connectors").status_code == 401


def test_create_redacts_secret_audits_and_lists(client, adm, db_session):
    r = client.post("/admin/connectors", headers=adm, json=body(schedule_minutes=60))
    assert r.status_code == 201
    out = r.json()
    assert out["config"]["secret_key"] == "***" and out["config"]["access_key"] == "AK"
    assert db_session.get(Connector, uuid.UUID(out["id"])).config["secret_key"] == "SECRET"
    assert "SECRET" not in str([a.detail for a in db_session.scalars(select(AuditLog))])
    got = client.get(f"/admin/connectors/{out['id']}", headers=adm).json()
    assert got["config"]["secret_key"] == "***" and got["last_run"] is None
    assert "SECRET" not in client.get("/admin/connectors", headers=adm).text


def test_create_validation(client, adm):
    for bad in (
        body(visibility="shared"),
        body(visibility="org_unit"),
        body(visibility="org_unit", org_unit="Physics"),
        body(schedule_minutes=4),
        body(schedule_minutes=10081),
        body(kind="http"),
        body(config={**S3, "endpoint": "ftp://x"}),
        body(config={**S3, "bogus": 1}),
        {k: v for k, v in body().items() if k != "visibility"},
    ):
        assert client.post("/admin/connectors", headers=adm, json=bad).status_code == 422, bad
    ok = client.post(
        "/admin/connectors",
        headers=adm,
        json=body(visibility="org_unit", org_unit="/Physics", schedule_minutes=5),
    )
    assert ok.status_code == 201 and ok.json()["org_unit"] == "/Physics"


def test_duplicate_name_409(client, adm):
    assert client.post("/admin/connectors", headers=adm, json=body()).status_code == 201
    assert client.post("/admin/connectors", headers=adm, json=body()).status_code == 409


def test_patch_keeps_masked_secret_and_changes_schedule(client, adm, db_session):
    cid = client.post("/admin/connectors", headers=adm, json=body()).json()["id"]
    r = client.patch(
        f"/admin/connectors/{cid}",
        headers=adm,
        json={"config": {**S3, "secret_key": "***", "bucket": "b2"}, "schedule_minutes": 10},
    )
    assert (
        r.status_code == 200 and r.json()["config"]["bucket"] == "b2" and r.json()["schedule_minutes"] == 10
    )
    assert db_session.get(Connector, uuid.UUID(cid)).config["secret_key"] == "SECRET"
    client.patch(f"/admin/connectors/{cid}", headers=adm, json={"config": {**S3, "secret_key": "NEW"}})
    assert db_session.get(Connector, uuid.UUID(cid)).config["secret_key"] == "NEW"
    assert (
        client.patch(f"/admin/connectors/{cid}", headers=adm, json={"schedule_minutes": None}).json()[
            "schedule_minutes"
        ]
        is None
    )
    assert (
        client.patch(f"/admin/connectors/{cid}", headers=adm, json={"visibility": "org_unit"}).status_code
        == 422
    )
    assert client.patch(f"/admin/connectors/{uuid.uuid4()}", headers=adm, json={}).status_code == 404


def test_delete_keeps_documents(
    client, adm, db_session, session_factory, settings, store, s3_client, test_bucket, prefix
):
    cid = client.post("/admin/connectors", headers=adm, json=body()).json()["id"]
    c = db_session.get(Connector, uuid.UUID(cid))
    owner = db_session.get(User, c.owner_id)
    put(s3_client, test_bucket, prefix + "a", b"keep me")
    c.config = {**c.config, "endpoint": settings.s3_endpoint, "bucket": test_bucket, "prefix": prefix,
                "access_key": settings.s3_access_key, "secret_key": settings.s3_secret_key}  # fmt: skip
    db_session.commit()
    assert run(db_session, session_factory, settings, store, c).stats["ingested"] == 1
    assert client.delete(f"/admin/connectors/{cid}", headers=adm).status_code == 204
    db_session.expire_all()
    d = db_session.scalar(select(Document))
    assert d is not None and d.connector_id is None and d.owner_id == owner.id
    assert db_session.scalar(select(ConnectorItem.key)) is None
    assert client.get(f"/admin/connectors/{cid}", headers=adm).status_code == 404


def test_run_endpoint_409_when_active_and_runs_history(client, adm, db_session):
    cid = client.post("/admin/connectors", headers=adm, json=body()).json()["id"]
    r = client.post(f"/admin/connectors/{cid}/run", headers=adm)
    assert r.status_code == 202
    job = db_session.get(Job, r.json()["job_id"])
    assert job.kind == "connector_run" and job.payload == {"connector_id": cid}
    assert client.post(f"/admin/connectors/{cid}/run", headers=adm).status_code == 409
    job.status = "running"
    db_session.commit()
    assert client.post(f"/admin/connectors/{cid}/run", headers=adm).status_code == 409
    job.status = "done"
    db_session.commit()
    assert client.post(f"/admin/connectors/{cid}/run", headers=adm).status_code == 202
    for i in range(3):
        db_session.add(ConnectorRun(connector_id=uuid.UUID(cid), status="ok", log=f"l{i}"))
    db_session.commit()
    page = client.get(f"/admin/connectors/{cid}/runs?limit=2", headers=adm).json()
    assert len(page["items"]) == 2 and page["next_before_id"] and "log" not in page["items"][0]
    rest = client.get(f"/admin/connectors/{cid}/runs?before_id={page['next_before_id']}", headers=adm).json()
    assert len(rest["items"]) == 1 and rest["next_before_id"] is None
    one = client.get(f"/admin/connectors/{cid}/runs/{page['items'][0]['id']}", headers=adm).json()
    assert one["log"] == "l2"
    other = client.post("/admin/connectors", headers=adm, json=body(name="n2")).json()["id"]
    assert (
        client.get(f"/admin/connectors/{other}/runs/{page['items'][0]['id']}", headers=adm).status_code == 404
    )


def test_fs_create_validates_roots(client, adm, app, settings, tmp_path):
    root = tmp_path / "r"
    (root / "d").mkdir(parents=True)
    out = tmp_path / "out"
    out.mkdir()
    os.symlink(out, root / "lnk")
    fsb = lambda p: {"name": "f", "kind": "fs", "config": {"path": str(p)}, "visibility": "private"}
    assert (
        client.post("/admin/connectors", headers=adm, json=fsb(root)).status_code == 422
    )  # no roots configured
    app.state.settings = settings.model_copy(update={"connector_fs_roots": [str(root)]})
    for bad in (out, root / ".." / "out", root / "lnk", "relative/path", root / "missing"):
        assert client.post("/admin/connectors", headers=adm, json=fsb(bad)).status_code == 422, bad
    assert client.post("/admin/connectors", headers=adm, json=fsb(root / "d")).status_code == 201


def test_roots_env_parsing(monkeypatch, settings):
    from roracle.config import Settings

    monkeypatch.setenv("ROR_CONNECTOR_FS_ROOTS", "/a, /b")
    assert Settings().connector_fs_roots == ["/a", "/b"]
    monkeypatch.setenv("ROR_CONNECTOR_FS_ROOTS", '["/c"]')
    assert Settings().connector_fs_roots == ["/c"]
    monkeypatch.delenv("ROR_CONNECTOR_FS_ROOTS")
    assert Settings().connector_fs_roots == []


def test_item_fence_is_the_last_statement_before_the_commit_not_held_across_the_upload(
    db_session, session_factory, settings, store, s3_client, test_bucket, prefix, owner, monkeypatch
):
    """`fence()` takes FOR SHARE on the job row: held across the MinIO put it would stall the lease heartbeat."""
    from roracle.connectors import run as run_mod

    put(s3_client, test_bucket, prefix + "a", b"fence late")
    order = []
    real_add, real_fence = run_mod.add_document, run_mod.fence

    def add(*a, **k):
        out = real_add(*a, **k)  # includes the blob upload
        order.append("add_document")
        return out

    def fence(s):
        order.append("fence")
        return real_fence(s)

    monkeypatch.setattr(run_mod, "add_document", add)
    monkeypatch.setattr(run_mod, "fence", fence)
    c = s3_connector(db_session, owner, settings, test_bucket, prefix)
    assert run(db_session, session_factory, settings, store, c).stats["ingested"] == 1
    assert order == ["add_document", "fence"]  # fence AFTER the upload, immediately before the commit
