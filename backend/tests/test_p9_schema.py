import pytest
from alembic import command
from conftest import _alembic_config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from roracle.models import Document
from roracle.models_writing import Draft, DraftVersion, WritingProfile

TABLES = {"drafts", "draft_versions", "writing_profiles"}


def _draft(db, user):
    d = Document(owner_id=user.id, title="t", org_unit="/", visibility="private")
    db.add(d)
    db.flush()
    db.add(Draft(document_id=d.id))
    db.flush()
    return d


def test_defaults_and_cascade(db_session, user):
    d = _draft(db_session, user)
    dr = db_session.get(Draft, d.id)
    db_session.refresh(dr)
    assert (dr.status, dr.coauthors, dr.acks) == ("draft", {"users": [], "groups": []}, [])
    db_session.add(DraftVersion(document_id=d.id, version=1, content="x", author_id=user.id))
    db_session.flush()
    db_session.delete(d)
    db_session.flush()
    db_session.expire_all()
    assert db_session.get(Draft, d.id) is None
    assert db_session.scalar(text("SELECT count(*) FROM draft_versions")) == 0


def test_status_check(db_session, user):
    d = _draft(db_session, user)
    db_session.execute(text("UPDATE drafts SET status='draft' WHERE document_id=:i"), {"i": d.id})
    with pytest.raises(IntegrityError):
        db_session.execute(text("UPDATE drafts SET status='weird' WHERE document_id=:i"), {"i": d.id})


def test_version_unique(db_session, user):
    d = _draft(db_session, user)
    db_session.add_all(
        [DraftVersion(document_id=d.id, version=1, content="x", author_id=user.id) for _ in range(2)]
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_profile_unique_and_default_active(db_session):
    wp = WritingProfile(name="a", version=1, yaml="", spec={})
    db_session.add(wp)
    db_session.flush()
    db_session.refresh(wp)
    assert wp.active is True
    db_session.add(WritingProfile(name="a", version=1, yaml="", spec={}))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_migration_down_and_up(session_factory, db_session):
    db_session.rollback()
    cfg = _alembic_config()
    command.downgrade(cfg, "0009")
    try:
        with session_factory() as s:
            assert not TABLES & set(inspect(s.connection()).get_table_names())
    finally:
        command.upgrade(cfg, "head")
    with session_factory() as s:
        assert TABLES <= set(inspect(s.connection()).get_table_names())
