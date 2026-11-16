"""0011 upgrade on pre-existing data: co-author backfill (+acl_sync) and is_latest repair."""

import uuid

from alembic import command
from api_seed import seed
from conftest import _alembic_config
from sqlalchemy import select, text

from roracle.models import Document, DocumentCoauthor, DocumentVersion, Job, User
from roracle.models_writing import Draft


def _user(db, name):
    u = User(id=uuid.uuid4(), sub=name, username=name)
    db.add(u)
    db.flush()
    return u.id


def test_backfill_and_is_latest_repair(session_factory, db_session):
    owner, co, other = (_user(db_session, n) for n in ("o", "c", "x"))
    ghost = str(uuid.uuid4())  # unknown user id in a draft: skipped, not an FK error
    pub, _ = seed(db_session, owner, visibility="everyone", stage="searchable")
    db_session.add(Draft(document_id=pub.id, status="submitted", submitted_version=1,
                         coauthors={"users": [str(co), str(owner), ghost], "groups": []}))  # fmt: skip
    wip, _ = seed(db_session, owner, visibility="private", stage="uploaded")  # unpublished draft: no grant
    db_session.add(Draft(document_id=wip.id, status="draft", coauthors={"users": [str(other)], "groups": []}))
    multi, _ = seed(db_session, owner, stage="searchable", versions=3)  # is_latest only on v3 here
    vs = {
        v.version: v
        for v in db_session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == multi.id))
    }
    vs[2].stage, vs[3].stage = "searchable", "review"  # newest is not searchable
    vs[1].is_latest = vs[2].is_latest = vs[3].is_latest = True  # corrupted: all flagged
    db_session.commit()
    pub_id, wip_id, multi_id = pub.id, wip.id, multi.id
    db_session.rollback()  # release locks before DDL
    cfg = _alembic_config()
    command.downgrade(cfg, "0010")
    command.upgrade(cfg, "head")
    db_session.expire_all()
    rows = db_session.execute(select(DocumentCoauthor.document_id, DocumentCoauthor.user_id)).all()
    assert rows == [(pub_id, co)]
    jobs = db_session.scalars(select(Job).where(Job.kind == "acl_sync")).all()
    assert [j.payload for j in jobs] == [{"doc_id": str(pub_id)}] and wip_id
    flags = {
        v.version: v.is_latest
        for v in db_session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == multi_id))
    }
    assert flags == {1: False, 2: True, 3: False}
    assert db_session.get(Document, pub_id) is not None
    default = db_session.scalar(
        text("select column_default from information_schema.columns "
             "where table_name='document_versions' and column_name='is_latest'")
    )  # fmt: skip
    assert default == "false"
    db_session.rollback()
    command.downgrade(cfg, "0010")  # downgrade restores the old default
    command.upgrade(cfg, "head")  # and re-running the upgrade is idempotent
    db_session.expire_all()
    assert len(db_session.scalars(select(DocumentCoauthor)).all()) == 1
