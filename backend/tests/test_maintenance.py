import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import bearer
from ingest_helpers import make_version
from sqlalchemy import func, select, text

from roracle import maintenance
from roracle.models import (
    Annotation,
    AuditLog,
    Blob,
    Document,
    DocumentVersion,
    Evidence,
    Job,
)


class FakeEs:
    def __init__(self):
        self.deleted = []

    def delete_by_query(self, **kw):
        self.deleted.append(kw["query"]["term"]["doc_id"])
        return {"failures": []}


class FakeClient:
    def __init__(self, fresh=()):
        self.deleted, self.fresh = [], set(fresh)

    def head_object(self, Bucket, Key):
        age = timedelta(minutes=5) if Key in self.fresh else timedelta(days=3)
        return {"LastModified": datetime.now(UTC) - age}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


def fake_store(fresh=()):
    return SimpleNamespace(bucket="b", client=FakeClient(fresh), key=lambda sha: f"blobs/{sha[:2]}/{sha}")


def settings(days=30):
    return SimpleNamespace(retention_deleted_days=days, maintenance_interval_s=100)


def age_blobs(db, days=3):
    db.execute(text(f"UPDATE blobs SET created_at = now() - interval '{days} days'"))
    db.commit()


def trash(db, doc, days):
    doc.deleted_at = datetime.now(UTC) - timedelta(days=days)
    db.commit()


def jobs(db, kind):
    return list(db.scalars(select(Job).where(Job.kind == kind).order_by(Job.id)))


def test_purge_respects_age_and_leaves_live_docs(db_session, user, store):
    old = make_version(db_session, store, user)
    recent = make_version(db_session, store, user)
    live = make_version(db_session, store, user)
    trash(db_session, db_session.get(Document, old.document_id), 40)
    trash(db_session, db_session.get(Document, recent.document_id), 5)
    es = FakeEs()
    maintenance.purge(db_session, es, settings(30), store)
    db_session.commit()
    assert es.deleted == [str(old.document_id)]
    assert db_session.get(Document, old.document_id) is None
    assert db_session.get(DocumentVersion, old.id) is None
    assert db_session.get(Document, recent.document_id) is not None
    assert db_session.get(Document, live.document_id) is not None
    assert db_session.get(Blob, old.blob_sha256).refcount == 0
    assert db_session.get(Blob, live.blob_sha256).refcount == 1


def test_purge_off_when_days_zero(db_session, user, store):
    v = make_version(db_session, store, user)
    trash(db_session, db_session.get(Document, v.document_id), 400)
    es = FakeEs()
    maintenance.purge(db_session, es, settings(0), store)
    assert es.deleted == [] and db_session.get(Document, v.document_id) is not None


def test_purge_removes_notes_and_near_dup_links(db_session, user, store):
    old = make_version(db_session, store, user)
    other = make_version(db_session, store, user)
    other.near_dup_of = old.id
    db_session.add(
        Annotation(
            id=uuid.uuid4(), document_id=old.document_id, version_id=old.id, author_id=user.id,
            kind="comment", body="x", visibility="everyone",
        )
    )  # fmt: skip
    db_session.commit()
    trash(db_session, db_session.get(Document, old.document_id), 40)
    maintenance.purge(db_session, FakeEs(), settings(30), store)
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(Annotation)) == 0
    db_session.refresh(other)
    assert other.near_dup_of is None


def test_gc_keeps_blob_shared_by_live_and_purged_version(db_session, user, store):
    a = make_version(db_session, store, user)
    b = make_version(db_session, store, user)
    b.blob_sha256 = a.blob_sha256  # same content, two documents
    db_session.commit()
    trash(db_session, db_session.get(Document, a.document_id), 40)
    maintenance.purge(db_session, FakeEs(), settings(30), store)
    db_session.commit()
    age_blobs(db_session)
    fs = fake_store()
    maintenance.gc_blobs(db_session, fs)
    db_session.commit()
    assert db_session.get(Blob, a.blob_sha256) is not None
    assert store.key(a.blob_sha256) not in fs.client.deleted


def test_gc_deletes_orphans_only_when_old_unreferenced_and_s3_idle(db_session, user, store):
    kept = make_version(db_session, store, user)
    orphan = Blob(sha256="b" * 64, size=1, refcount=0)
    young = Blob(sha256="c" * 64, size=1, refcount=0)
    fresh = Blob(sha256="d" * 64, size=1, refcount=0)
    ev = Blob(sha256="e" * 64, size=1, refcount=0)
    db_session.add_all([orphan, young, fresh, ev])
    ann = Annotation(
        id=uuid.uuid4(), document_id=kept.document_id, version_id=kept.id, author_id=user.id,
        kind="comment", body="x", visibility="everyone",
    )  # fmt: skip
    db_session.add(ann)
    db_session.flush()
    db_session.add(
        Evidence(annotation_id=ann.id, creator_id=user.id, stance="supports", crate={}, blob_sha256="e" * 64)
    )
    db_session.commit()
    age_blobs(db_session)
    db_session.execute(text("UPDATE blobs SET created_at = now() WHERE sha256 = :s"), {"s": "c" * 64})
    db_session.commit()
    fs = fake_store(fresh={f"blobs/dd/{'d' * 64}"})
    assert maintenance.gc_blobs(db_session, fs) == 1
    db_session.commit()
    assert fs.client.deleted == [f"blobs/bb/{'b' * 64}"]
    assert db_session.get(Blob, "b" * 64) is None
    for sha in ("c" * 64, "d" * 64, "e" * 64, kept.blob_sha256):
        assert db_session.get(Blob, sha) is not None
    assert not any(k.startswith("tmp/") for k in fs.client.deleted)


def test_gc_skips_row_locked_by_concurrent_upload(db_session, session_factory):
    db_session.add(Blob(sha256="f" * 64, size=1, refcount=0))
    db_session.commit()
    age_blobs(db_session)
    fs = fake_store()
    with session_factory() as other:  # an upload's upsert holds the row lock
        other.execute(select(Blob).where(Blob.sha256 == "f" * 64).with_for_update())
        assert maintenance.gc_blobs(db_session, fs) == 0
        other.rollback()
    db_session.rollback()
    assert fs.client.deleted == [] and db_session.get(Blob, "f" * 64) is not None


def test_gc_missing_s3_object_still_drops_row(db_session):
    db_session.add(Blob(sha256="9" * 64, size=1, refcount=0))
    db_session.commit()
    age_blobs(db_session)
    fs = fake_store()

    def gone(Bucket, Key):
        raise maintenance.ClientError({"Error": {"Code": "404"}}, "HeadObject")

    fs.client.head_object = gone
    assert maintenance.gc_blobs(db_session, fs) == 1
    db_session.commit()
    assert db_session.get(Blob, "9" * 64) is None


def test_reconcile_one_acl_sync_per_live_doc_and_resumes(db_session, user, monkeypatch):
    monkeypatch.setattr(maintenance, "RECONCILE_BATCH", 2)
    ids = []
    for i in range(5):
        d = Document(owner_id=user.id, title=str(i), org_unit="/", visibility="private")
        db_session.add(d)
        db_session.flush()
        ids.append(d.id)
    gone = Document(owner_id=user.id, title="x", org_unit="/", visibility="private", deleted_at=func.now())
    db_session.add(gone)
    db_session.commit()
    maintenance.handle_reconcile_tags(db_session, {})
    first = jobs(db_session, "acl_sync")
    assert len(first) == 2
    nxt = jobs(db_session, "reconcile_tags")
    assert len(nxt) == 1 and nxt[0].payload["after"] == first[-1].payload["doc_id"]
    maintenance.handle_reconcile_tags(db_session, nxt[0].payload)
    maintenance.handle_reconcile_tags(db_session, jobs(db_session, "reconcile_tags")[-1].payload)
    synced = [j.payload["doc_id"] for j in jobs(db_session, "acl_sync")]
    assert sorted(synced) == sorted(str(i) for i in ids) and len(synced) == 5
    assert str(gone.id) not in synced
    assert len(jobs(db_session, "reconcile_tags")) == 2  # last batch (1 < 2) does not chain


def test_periodic_maintenance_runs_all_and_reschedules(db_session, user, store):
    v = make_version(db_session, store, user)
    trash(db_session, db_session.get(Document, v.document_id), 40)
    fs, es = fake_store(), FakeEs()
    maintenance.handle_maintenance(db_session, {"op": "all"}, settings=settings(30), store=fs, es=es)
    db_session.commit()
    assert es.deleted == [str(v.document_id)]
    again = [j for j in jobs(db_session, "maintenance") if j.payload == {"op": "all"}]
    assert len(again) == 1 and again[0].status == "queued"
    assert len(jobs(db_session, "reconcile_tags")) == 1
    maintenance.handle_maintenance(db_session, {"op": "all"}, settings=settings(30), store=fs, es=es)
    assert len([j for j in jobs(db_session, "maintenance") if j.payload == {"op": "all"}]) == 1


def test_manual_op_does_not_reschedule(db_session):
    maintenance.handle_maintenance(
        db_session, {"op": "gc"}, settings=settings(0), store=fake_store(), es=FakeEs()
    )
    assert jobs(db_session, "maintenance") == []


def test_ensure_maintenance_is_idempotent(db_session, session_factory):
    maintenance.ensure_maintenance(session_factory)
    maintenance.ensure_maintenance(session_factory)
    db_session.rollback()
    assert len(jobs(db_session, "maintenance")) == 1


@pytest.fixture
def adm(make_token):
    return bearer(make_token(sub="adm", username="adm", roles=("roracle-admin",)))


@pytest.mark.parametrize("op", ["purge", "gc", "reconcile"])
def test_admin_endpoint_enqueues_and_audits(client, adm, db_session, op):
    r = client.post(f"/admin/maintenance/{op}", headers=adm)
    assert r.status_code == 202
    j = db_session.get(Job, r.json()["job_id"])
    assert j.kind == "maintenance" and j.payload == {"op": op}
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == f"admin.maintenance_{op}"))


def test_admin_endpoint_rejects_unknown_op_and_non_admin(client, adm, as_ana):
    assert client.post("/admin/maintenance/nuke", headers=adm).status_code == 422
    assert client.post("/admin/maintenance/gc", headers=as_ana).status_code == 403


def test_intake_locks_blob_row_before_s3_write(db_session, user):
    from roracle.ingest import intake
    from roracle.storage import StagedBlob

    seen = []

    class Store:
        def promote(self, staged, ct):
            seen.append(db_session.scalar(select(Blob.refcount).where(Blob.sha256 == staged.sha256)))

    staged = StagedBlob(upload=None, sha256="a1" * 32, size=3, filename="f", content_type="text/plain")
    intake.add_document(
        db_session, Store(), staged, owner_id=user.id, org_unit="", visibility="private",
        filename="f", content_type="text/plain",
    )  # fmt: skip
    assert seen == [1]  # row (and its lock) existed before the copy


def test_purge_poison_doc_is_skipped_and_others_purged(db_session, user, store):
    bad = make_version(db_session, store, user)
    good = make_version(db_session, store, user)
    for v in (bad, good):
        trash(db_session, db_session.get(Document, v.document_id), 40)

    class Es(FakeEs):
        def delete_by_query(self, **kw):
            if kw["query"]["term"]["doc_id"] == str(bad.document_id):
                raise RuntimeError("boom")
            return super().delete_by_query(**kw)

    maintenance.purge(db_session, Es(), settings(), store)
    assert db_session.get(Document, bad.document_id) is not None
    assert db_session.get(Document, good.document_id) is None


def test_purge_decrements_evidence_blob_refcount(db_session, user, store):
    v = make_version(db_session, store, user)
    note = Annotation(
        id=uuid.uuid4(), document_id=v.document_id, version_id=v.id, author_id=user.id,
        kind="comment", body="x", visibility="everyone",
    )  # fmt: skip
    db_session.add(note)
    db_session.add(Blob(sha256="4" * 64, size=1, refcount=1))
    db_session.flush()
    db_session.add(
        Evidence(annotation_id=note.id, creator_id=user.id, stance="supports", crate={}, blob_sha256="4" * 64)
    )
    trash(db_session, db_session.get(Document, v.document_id), 40)
    maintenance.purge(db_session, FakeEs(), settings(), store)
    assert db_session.get(Blob, "4" * 64).refcount == 0


def test_purge_deletes_derived_objects(db_session, user, store, s3):
    v = make_version(db_session, store, user)
    keep = make_version(db_session, store, user)
    for x in (v, keep):
        for name in ("rendition.pdf", "docling.json"):
            store.put_bytes(f"derived/{x.id}/{name}", b"text", "application/octet-stream")
    trash(db_session, db_session.get(Document, v.document_id), 40)
    maintenance.purge(db_session, FakeEs(), settings(30), store)
    keys = s3.keys()
    assert not [k for k in keys if k.startswith(f"derived/{v.id}/")]
    assert f"derived/{keep.id}/docling.json" in keys


def test_delete_prefix_logs_errors(caplog):
    class Pager:
        def paginate(self, **kw):
            return [{"Contents": [{"Key": "derived/x/a"}]}]

    class Client:
        def get_paginator(self, name):
            return Pager()

        def delete_objects(self, **kw):
            return {"Errors": [{"Key": "derived/x/a", "Code": "AccessDenied"}]}

    store = SimpleNamespace(client=Client(), bucket="b")
    with caplog.at_level("WARNING"):
        maintenance._delete_prefix(store, "derived/x/")
    assert "failed for 1 keys" in caplog.text


class _Indices:
    def __init__(self, alias, index):
        self.alias, self.index = alias, index

    def exists_alias(self, name):
        return self.alias

    def exists(self, index):
        return self.index


def _missing_es(alias, index):
    from elasticsearch import NotFoundError

    class Es(FakeEs):
        indices = _Indices(alias, index)

        def delete_by_query(self, **kw):
            raise NotFoundError("nf", SimpleNamespace(status=404), {})

    return Es()


@pytest.mark.parametrize(("alias", "index", "purged"), [(False, False, True), (False, True, False)])
def test_purge_missing_alias_only_ignored_when_nothing_indexed(db_session, user, store, alias, index, purged):
    v = make_version(db_session, store, user)
    trash(db_session, db_session.get(Document, v.document_id), 40)
    maintenance.purge(db_session, _missing_es(alias, index), settings(30), store)
    assert (db_session.get(Document, v.document_id) is None) is purged


def test_periodic_reschedule_survives_failed_step_and_ensure(db_session, user, session_factory, monkeypatch):
    monkeypatch.setattr(maintenance, "purge", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    maintenance.handle_maintenance(
        db_session, {"op": "all"}, settings=settings(), store=fake_store(), es=FakeEs()
    )
    assert any(j.payload == {"op": "all"} for j in jobs(db_session, "maintenance"))
