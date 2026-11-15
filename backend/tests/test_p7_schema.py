from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from roracle.models import AuditLog, Handoff, HandoffShare, PersonalAccessToken, User

EXP = datetime.now(UTC) + timedelta(days=1)


def _handoff(user, code="abcd1234", **kw):
    return Handoff(code=code, creator_id=user.id, kind="chat", expires_at=EXP, **kw)


@pytest.fixture
def handoff(db_session, user):
    h = _handoff(user)
    db_session.add(h)
    db_session.flush()
    return h


def test_defaults(db_session, handoff):
    db_session.refresh(handoff)
    assert (handoff.audience, handoff.created_via, handoff.source, handoff.notes) == ("creator", "ui", {}, [])


@pytest.mark.parametrize("kw", [{"kind": "bogus"}, {"audience": "everyone"}, {"created_via": "cli"}])
def test_bad_handoff_values(db_session, user, kw):
    h = _handoff(user)
    for k, v in kw.items():
        setattr(h, k, v)
    db_session.add(h)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_bad_principal_kind(db_session, handoff):
    db_session.add(HandoffShare(handoff_code=handoff.code, principal_kind="team", principal="x"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_delete_handoff_cascades_shares(db_session, handoff):
    db_session.add(HandoffShare(handoff_code=handoff.code, principal_kind="user", principal="bob"))
    db_session.flush()
    db_session.execute(delete(Handoff).where(Handoff.code == handoff.code))
    assert db_session.scalar(select(func.count()).select_from(HandoffShare)) == 0


def _pat(user, h="h" * 64):
    return PersonalAccessToken(user_id=user.id, name="n", prefix="ror_abc", token_hash=h, expires_at=EXP)


def test_duplicate_token_hash(db_session, user):
    db_session.add_all([_pat(user), _pat(user)])
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_user_delete_cascades_tokens_and_nulls_audit(db_session, user):
    t = _pat(user)
    a = AuditLog(user_id=user.id, action="x")
    db_session.add_all([t, a])
    db_session.flush()
    db_session.refresh(t)
    assert t.scopes == ["read"]
    db_session.execute(delete(User).where(User.id == user.id))
    assert db_session.scalar(select(func.count()).select_from(PersonalAccessToken)) == 0
    db_session.expire_all()
    assert db_session.get(AuditLog, a.id).user_id is None
