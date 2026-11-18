import pytest
from alembic.script import ScriptDirectory
from conftest import _alembic_config
from sqlalchemy import event

from roracle import onboarding


@pytest.fixture
def uid(user):
    return user.id


def test_mark_appends_once(db_session, uid):
    onboarding.mark(db_session, uid, "search")
    onboarding.mark(db_session, uid, "search")
    assert onboarding.state(db_session, uid)["steps"] == ["search"]


def test_mark_order_and_state(db_session, uid):
    assert onboarding.state(db_session, uid) == {"steps": [], "dismissed": False}
    onboarding.mark(db_session, uid, "collect")
    onboarding.mark(db_session, uid, "search")
    assert onboarding.state(db_session, uid)["steps"] == ["search", "collect"]


def test_rollback_not_cached(db_session, uid):
    db_session.commit()  # keep the user; only the mark is rolled back
    onboarding.mark(db_session, uid, "search")
    db_session.rollback()
    assert onboarding.state(db_session, uid)["steps"] == []
    onboarding.mark(db_session, uid, "search")
    db_session.commit()
    assert onboarding.state(db_session, uid)["steps"] == ["search"]


def test_known_cache_skips_db(db_session, uid):
    onboarding.mark(db_session, uid, "ask")
    onboarding.mark(db_session, uid, "ask")
    db_session.commit()  # cached only after commit
    n = []
    engine = db_session.get_bind()
    listener = lambda *a: n.append(1)
    event.listen(engine, "before_cursor_execute", listener)
    try:
        onboarding.mark(db_session, uid, "ask")
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert n == []


def test_mark_rollback_mark_commit_is_cached(db_session, uid):
    db_session.commit()
    onboarding.mark(db_session, uid, "search")
    onboarding.mark(db_session, uid, "search")
    db_session.rollback()
    onboarding.mark(db_session, uid, "search")
    db_session.commit()
    assert onboarding.state(db_session, uid)["steps"] == ["search"]
    assert (uid, "search") in onboarding._known


def test_unknown_step_raises(db_session, uid):
    with pytest.raises(ValueError):
        onboarding.mark(db_session, uid, "nope")


def test_set_dismissed_roundtrip(db_session, uid):
    onboarding.set_dismissed(db_session, uid, True)
    assert onboarding.state(db_session, uid)["dismissed"] is True
    onboarding.mark(db_session, uid, "upload")
    onboarding.set_dismissed(db_session, uid, False)
    assert onboarding.state(db_session, uid) == {"steps": ["upload"], "dismissed": False}


def test_migration_single_head():
    assert len(ScriptDirectory.from_config(_alembic_config()).get_heads()) == 1
