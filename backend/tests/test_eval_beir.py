import json
from pathlib import Path

import pytest
from sqlalchemy import select

from roracle.eval import beir
from roracle.ingest.pipeline import docling_key
from roracle.models import AuditLog, Document, DocumentShare, DocumentVersion, Job, User
from roracle.models_eval import EvalQuery, EvalSet

DIR = Path(__file__).parent / "fixtures" / "beir_mini"


def mk(db, name, admin=False, groups=()):
    u = User(sub=name, username=name, is_admin=admin, groups=list(groups))
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def owner(db_session):
    return mk(db_session, "owen")


def imp(db, store, settings, **kw):
    kw = {"set_name": "sf", "owner": "owen", **kw}
    return beir.import_beir(db, store, settings, DIR, **kw)


def test_import_hands_real_documents_to_ingest(db_session, store, settings, owner):
    out = imp(db_session, store, settings)
    assert out == {
        "documents": 3,
        "reused": 0,
        "requeued": 0,
        "queries": 1,
        "near_dup_kept": 0,
        "near_dup_parked": 0,
    }
    docs = db_session.scalars(select(Document).where(Document.owner_id == owner.id)).all()
    assert {d.visibility for d in docs} == {"private"} and {d.title for d in docs} == {
        "Alpha study",
        "Beta study",
        "d3",
    }
    vs = db_session.scalars(select(DocumentVersion)).all()
    assert {v.stage for v in vs} == {"parsed"} and len(
        db_session.scalars(select(Job).where(Job.kind == "ingest")).all()
    ) == 3
    v = next(v for v in vs if v.filename == "d1.txt")
    assert store.get_bytes(store.key(v.blob_sha256)).decode().startswith("Alpha particles")
    assert json.loads(store.get_bytes(docling_key(v.id)))["texts"]
    s = db_session.scalar(select(EvalSet).where(EvalSet.name == "sf"))
    q = db_session.scalar(select(EvalQuery).where(EvalQuery.set_id == s.id))
    d1 = next(d for d in docs if d.title == "Alpha study")
    assert (q.key, q.principals, q.relevant) == ("beir-q1", [owner.id], [{"doc": str(d1.id), "grade": 1}])
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "eval.import_beir")) is not None


def test_rerun_and_second_set_reuse_documents(db_session, store, settings, owner):
    imp(db_session, store, settings)
    again = imp(db_session, store, settings)  # resume: nothing new, unfinished versions re-queued
    assert (again["documents"], again["reused"], again["requeued"], again["queries"]) == (0, 3, 3, 0)
    other = imp(db_session, store, settings, set_name="sf2")
    assert (other["documents"], other["reused"], other["queries"]) == (0, 3, 1)
    assert len(db_session.scalars(select(Document).where(Document.owner_id == owner.id)).all()) == 3


def test_principals_and_groups_get_shares_not_wider_visibility(db_session, store, settings, owner):
    a, b = mk(db_session, "ann"), mk(db_session, "bob")
    imp(db_session, store, settings, principals=["ann", "bob"], groups=["/lab"])
    assert {d.visibility for d in db_session.scalars(select(Document))} == {"shared"}
    shares = {(s.principal_kind, s.principal) for s in db_session.scalars(select(DocumentShare))}
    assert shares == {("user", str(a.id)), ("user", str(b.id)), ("group", "/lab")}
    q = db_session.scalar(select(EvalQuery))
    assert q.principals == [owner.id, a.id, b.id]


def test_refuses_admin_missing_and_cutover(db_session, store, settings, owner, tmp_path, monkeypatch):
    mk(db_session, "root", admin=True)
    with pytest.raises(ValueError, match="admin"):
        imp(db_session, store, settings, owner="root")
    with pytest.raises(ValueError, match="admin"):
        imp(db_session, store, settings, principals=["root"])
    with pytest.raises(ValueError, match="not found"):
        beir.import_beir(db_session, store, settings, tmp_path, set_name="x", owner="owen")
    with pytest.raises(ValueError, match="no user"):
        imp(db_session, store, settings, owner="nobody")
    db_session.add(EvalSet(name="mine"))
    db_session.flush()
    with pytest.raises(ValueError, match="not created by this importer"):
        imp(db_session, store, settings, set_name="mine")
    monkeypatch.setattr(beir.reembed, "running_job", lambda s: object())
    with pytest.raises(ValueError, match="re-embed"):
        imp(db_session, store, settings)


def test_reuse_adds_shares_for_new_principals(db_session, store, settings, owner):
    ann = mk(db_session, "ann")
    imp(db_session, store, settings)
    imp(db_session, store, settings, set_name="sf2", principals=["ann"], groups=["/lab"])
    docs = db_session.scalars(select(Document)).all()
    assert {d.visibility for d in docs} == {"shared"}
    shares = {
        (s.document_id, s.principal_kind, s.principal) for s in db_session.scalars(select(DocumentShare))
    }
    assert {(d.id, "user", str(ann.id)) for d in docs} | {(d.id, "group", "/lab") for d in docs} == shares


def test_refuses_org_unit_visibility(db_session, store, settings, owner):
    with pytest.raises(ValueError, match="whole organisation"):
        imp(db_session, store, settings, visibility="org_unit")


def test_keeps_own_parked_near_dups_only(db_session, store, settings, owner):
    imp(db_session, store, settings)
    vs = {v.filename: v for v in db_session.scalars(select(DocumentVersion))}
    stranger = mk(db_session, "sam")
    other = Document(owner_id=stranger.id, title="x", visibility="everyone", org_unit="/")
    db_session.add(other)
    db_session.flush()
    foreign = DocumentVersion(
        document_id=other.id, version=1, blob_sha256=vs["d1.txt"].blob_sha256, filename="x.pdf",
        content_type="application/pdf", size=1, stage="searchable", is_latest=True, meta={},
    )  # fmt: skip
    db_session.add(foreign)
    db_session.flush()
    vs["d2.txt"].stage, vs["d2.txt"].near_dup_of = "review", vs["d1.txt"].id  # own dataset: kept
    vs["d3.txt"].stage, vs["d3.txt"].near_dup_of = "review", foreign.id  # someone else's upload: stays
    db_session.flush()
    out = imp(db_session, store, settings)
    assert (out["near_dup_kept"], out["near_dup_parked"]) == (1, 1)
    assert (vs["d2.txt"].stage, vs["d2.txt"].ingest_attempts) == ("deduped", 0)
    assert vs["d3.txt"].stage == "review"
    last = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "eval.import_beir").order_by(AuditLog.id.desc())
    ).first()
    assert last.detail["near_dup_kept"] == 1
