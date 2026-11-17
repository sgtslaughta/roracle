import pytest
from alembic import command
from alembic.script import ScriptDirectory
from conftest import _alembic_config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from roracle.models_eval import EvalQuery, EvalSet


def test_second_reference_set_rejected(db_session):
    db_session.add_all([EvalSet(name="a", is_reference=True), EvalSet(name="b", is_reference=False)])
    db_session.flush()
    db_session.add(EvalSet(name="c", is_reference=True))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_query_constraints(db_session):
    s = EvalSet(name="a")
    db_session.add(s)
    db_session.flush()
    db_session.add(EvalQuery(set_id=s.id, key="k", text="t", kind="weird", principals=[], relevant=[]))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_migration_up_down_up_single_head(session_factory, db_session):
    db_session.commit()
    cfg = _alembic_config()
    command.downgrade(cfg, "0012")
    tables = lambda: {
        r[0] for r in db_session.execute(text("SELECT table_name FROM information_schema.tables"))
    }
    assert not {"eval_sets", "eval_queries", "eval_runs", "eval_results"} & tables()
    db_session.rollback()
    command.upgrade(cfg, "head")
    db_session.rollback()
    assert {"eval_sets", "eval_queries", "eval_runs", "eval_results"} <= tables()
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1
