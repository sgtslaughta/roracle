import pytest
from alembic import command
from alembic.script import ScriptDirectory
from api_seed import seed
from conftest import _alembic_config
from sqlalchemy import select, text
from sqlalchemy.exc import DataError, IntegrityError

from roracle.labels.policy import Policy
from roracle.models import Document, Job, User
from roracle.models_labels import Label


def _label(db, **kw):
    row = Label(**{"name": "L", "colour": "#112233", "rank": 5, **kw})
    db.add(row)
    return row


def test_new_documents_default_to_default_label(db_session, user):
    d = Document(owner_id=user.id, title="t", org_unit="/", visibility="private")
    db_session.add(d)
    db_session.flush()
    db_session.refresh(d)
    assert str(d.label_id) == Policy.load(db_session).default.id


@pytest.mark.parametrize(
    "kw",
    [
        {"colour": "red"},
        {"colour": "#12345"},
        {"name": ""},
        {"name": "x" * 41},
        {"name": "general"},
        {"rank": 0},
    ],
)
def test_label_constraints(db_session, kw):
    _label(db_session, **kw)
    with pytest.raises((IntegrityError, DataError)):
        db_session.flush()
        db_session.execute(text("SET CONSTRAINTS uq_labels_rank IMMEDIATE"))  # rank check is deferred


def test_second_default_rejected(db_session):
    _label(db_session, is_default=True)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_label_in_use_is_restricted(db_session, user):
    seed(db_session, user.id)
    db_session.flush()
    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM labels"))


def test_migration_backfills_and_round_trips(session_factory, db_session):
    owner = User(sub="o", username="o")
    db_session.add(owner)
    db_session.flush()
    doc, _ = seed(db_session, owner.id)
    gone = Document(
        owner_id=owner.id, title="g", org_unit="/", visibility="private", deleted_at=text("now()")
    )
    db_session.add(gone)
    db_session.commit()
    doc_id = doc.id
    db_session.rollback()
    cfg = _alembic_config()
    command.downgrade(cfg, "0011")
    cols = {
        r[0]
        for r in db_session.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name='documents'")
        )
    }
    db_session.rollback()
    assert "label_id" not in cols
    db_session.execute(text("DELETE FROM jobs"))
    db_session.commit()
    command.upgrade(cfg, "head")
    db_session.expire_all()
    general = db_session.scalar(select(Label))
    assert (general.name, general.rank, general.is_default) == ("General", 0, True)
    assert not (
        general.exclude_llm or general.block_agents or general.block_export or general.reason_on_lower
    )
    assert db_session.get(Document, doc_id).label_id == general.id
    assert db_session.scalar(select(Document.label_id).where(Document.title == "g")) == general.id
    jobs = db_session.scalars(select(Job).where(Job.kind == "acl_sync")).all()
    assert [j.payload["doc_id"] for j in jobs] == [str(doc_id)]
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1
