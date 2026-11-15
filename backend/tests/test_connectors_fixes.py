"""Connector review-round tests (dedup scoping, caps, lifecycle, PATCH semantics)."""

import uuid

import pytest
from sqlalchemy import select
from test_connectors import S3, body, put, run, s3_connector

from roracle.connectors.run import handle_connector_run, handle_connector_tick
from roracle.models import Connector, ConnectorItem, ConnectorRun, Document, Job


def test_private_user_doc_is_never_matched_or_leaked(
    db_session, session_factory, settings, store, s3_client, test_bucket, prefix, owner
):
    from api_seed import seed

    private, _ = seed(db_session, owner.id, visibility="private", data=b"same bytes")
    db_session.commit()
    put(s3_client, test_bucket, prefix + "a.pdf", b"same bytes")
    c = s3_connector(db_session, owner, settings, test_bucket, prefix)
    r = run(db_session, session_factory, settings, store, c)
    assert r.stats["ingested"] == 1 and r.stats["duplicate"] == 0
    item = db_session.scalar(select(ConnectorItem))
    assert item.document_id != private.id
    assert db_session.get(Document, item.document_id).connector_id == c.id
    assert str(private.id) not in r.log and str(private.id) not in str(r.stats)


def test_unchanged_items_do_not_use_the_item_cap(
    db_session, session_factory, settings, store, s3_client, test_bucket, prefix, owner
):
    for i in range(3):
        put(s3_client, test_bucket, f"{prefix}a{i}", f"old {i}".encode())
    c = s3_connector(db_session, owner, settings, test_bucket, prefix)
    assert run(db_session, session_factory, settings, store, c).stats["ingested"] == 3
    put(s3_client, test_bucket, prefix + "z-new", b"brand new")
    s = settings.model_copy(update={"connector_max_items": 2})
    r = run(db_session, session_factory, s, store, c)
    assert r.status == "ok" and r.stats["unchanged"] == 3 and r.stats["ingested"] == 1


def test_connector_deleted_mid_run_stops_quietly(
    db_session, session_factory, settings, store, s3_client, test_bucket, prefix, owner, monkeypatch
):
    from roracle.connectors import run as runmod

    put(s3_client, test_bucket, prefix + "a", b"gone soon")
    c = s3_connector(db_session, owner, settings, test_bucket, prefix)
    real = runmod.stage

    def stage_then_delete(*a, **k):
        out = real(*a, **k)
        with session_factory() as s:
            s.delete(s.get(Connector, c.id))
            s.commit()
        return out

    monkeypatch.setattr(runmod, "stage", stage_then_delete)
    handle_connector_run(
        db_session,
        {"connector_id": str(c.id)},
        settings=settings,
        store=store,
        session_factory=session_factory,
    )
    db_session.rollback()
    assert (
        db_session.scalar(select(Document.id)) is None and db_session.scalar(select(ConnectorRun.id)) is None
    )
    assert not [k for k in store.client.list_objects_v2(Bucket=store.bucket, Prefix="blobs/").get("Contents", [])
                if k["Key"].endswith(__import__("hashlib").sha256(b"gone soon").hexdigest())]  # fmt: skip


def test_stale_running_run_marked_failed_at_start(
    db_session, session_factory, settings, store, s3_client, test_bucket, prefix, owner
):
    c = s3_connector(db_session, owner, settings, test_bucket, prefix)
    db_session.add(ConnectorRun(connector_id=c.id, status="running"))
    db_session.commit()
    run(db_session, session_factory, settings, store, c)
    old = db_session.scalars(select(ConnectorRun).order_by(ConnectorRun.id)).first()
    assert old.status == "failed" and old.log == "worker interrupted" and old.finished_at is not None


def test_tick_survives_scan_failure_and_requeues(db_session, settings, monkeypatch):
    from roracle.connectors import run as runmod

    def boom(_):
        raise RuntimeError("bad")

    monkeypatch.setattr(runmod, "_enqueue_due", boom)
    handle_connector_tick(db_session, {}, settings=settings)
    assert db_session.scalar(select(Job.id).where(Job.kind == "connector_tick")) is not None


def test_patch_visibility_propagates_to_documents_and_syncs_acl(client, adm, db_session, user):
    cid = client.post("/admin/connectors", headers=adm, json=body()).json()["id"]
    from api_seed import seed

    d, _ = seed(db_session, user.id, visibility="everyone")
    d.connector_id = uuid.UUID(cid)
    other, _ = seed(db_session, user.id, visibility="everyone")
    db_session.commit()
    r = client.patch(
        f"/admin/connectors/{cid}", headers=adm, json={"visibility": "org_unit", "org_unit": "/Physics"}
    )
    assert r.status_code == 200
    db_session.expire_all()
    assert (d.visibility, d.org_unit) == ("org_unit", "/Physics") and other.visibility == "everyone"
    jobs = db_session.scalars(select(Job).where(Job.kind == "acl_sync")).all()
    assert [j.payload for j in jobs] == [{"doc_id": str(d.id)}]


def test_patch_config_merges_and_null_deletes(client, adm, db_session):
    cid = client.post("/admin/connectors", headers=adm, json=body()).json()["id"]
    r = client.patch(
        f"/admin/connectors/{cid}", headers=adm, json={"config": {"bucket": "b9", "access_key": None}}
    )
    assert r.status_code == 200
    cfg = db_session.get(Connector, uuid.UUID(cid)).config
    assert cfg["bucket"] == "b9" and cfg["secret_key"] == "SECRET" and cfg["access_key"] is None
    assert cfg["endpoint"] == S3["endpoint"]


@pytest.mark.parametrize("ep", ["http://u:p@minio:9000", "http://u@minio:9000", "file:///x"])
def test_endpoint_with_userinfo_or_bad_scheme_rejected(client, adm, ep):
    assert (
        client.post("/admin/connectors", headers=adm, json=body(config={**S3, "endpoint": ep})).status_code
        == 422
    )


def test_connector_documents_exempt_from_upload_quota(
    client, as_ana, ana, db_session, app, settings, _blob_store_override
):
    from api_seed import seed

    from roracle.auth import get_app_settings

    c = Connector(name="q", kind="fs", owner_id=ana, visibility="private", config={})
    db_session.add(c)
    db_session.flush()
    d, _ = seed(db_session, ana, data=b"x" * 500)
    d.connector_id = c.id
    db_session.commit()
    small = settings.model_copy(update={"upload_quota_bytes": 100})
    app.dependency_overrides[get_app_settings] = lambda: small
    r = client.post("/documents", headers=as_ana, files={"file": ("a.pdf", b"%PDF-1", "application/pdf")})
    assert r.status_code == 201
