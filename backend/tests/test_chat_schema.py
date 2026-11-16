import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from roracle.models import AnswerRating, Chat, ChatMessage, ChatShare


@pytest.fixture
def chat(db_session, user):
    c = Chat(owner_id=user.id)
    db_session.add(c)
    db_session.flush()
    return c


@pytest.fixture
def message(db_session, chat):
    m = ChatMessage(chat_id=chat.id, role="assistant", content="hi")
    db_session.add(m)
    db_session.flush()
    return m


def test_defaults(db_session, chat):
    db_session.refresh(chat)
    assert (chat.title, chat.scope, chat.pins, chat.excludes) == ("", "all", [], [])
    assert chat.updated_at is not None


@pytest.mark.parametrize("scope", ["all", "open", f"collection:{uuid.uuid4()}"])
def test_valid_scopes(db_session, user, scope):
    db_session.add(Chat(owner_id=user.id, scope=scope))
    db_session.flush()


def test_invalid_scope(db_session, user):
    db_session.add(Chat(owner_id=user.id, scope="everything"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_invalid_role(db_session, chat):
    db_session.add(ChatMessage(chat_id=chat.id, role="system"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_invalid_status(db_session, chat):
    db_session.add(ChatMessage(chat_id=chat.id, role="user", status="weird"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_invalid_share_kind(db_session, chat):
    db_session.add(ChatShare(chat_id=chat.id, principal_kind="team", principal="x"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_invalid_rating_value(db_session, message, user):
    db_session.add(AnswerRating(message_id=message.id, user_id=user.id, value=0))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_delete_chat_cascades(db_session, chat, message, user):
    db_session.add_all(
        [
            ChatShare(chat_id=chat.id, principal_kind="user", principal="bob"),
            AnswerRating(message_id=message.id, user_id=user.id, value=1, comment="good"),
        ]
    )
    db_session.flush()
    db_session.execute(Chat.__table__.delete().where(Chat.id == chat.id))
    for model in (ChatMessage, ChatShare, AnswerRating):
        assert db_session.scalar(select(func.count()).select_from(model)) == 0
