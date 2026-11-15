import pytest
from alembic import command
from conftest import _alembic_config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from roracle.models import AppSetting, Connector, ConnectorItem, ConnectorRun, Document, Notification

TABLES = {"connectors", "connector_runs", "connector_items", "notifications", "app_settings"}


def _connector(user, **kw):
    return Connector(**{"name": "c", "kind": "s3", "owner_id": user.id, "visibility": "everyone", **kw})


def test_defaults_and_links(db_session, user):
    c = _connector(user)
    db_session.add(c)
    db_session.flush()
    db_session.refresh(c)
    assert (c.config, c.org_unit, c.enabled, c.schedule_minutes) == ({}, "", True, None)
    d = Document(owner_id=user.id, title="t", org_unit="/", visibility="private", connector_id=c.id)
    db_session.add(d)
    db_session.flush()
    db_session.add_all(
        [
            ConnectorRun(connector_id=c.id, status="running"),
            ConnectorItem(connector_id=c.id, key="k", sha256="a" * 64, document_id=d.id),
            Notification(user_id=user.id, kind="x", payload={}),
            AppSetting(key="k", value=[1]),
        ]
    )
    db_session.flush()
    db_session.delete(c)  # runs/items cascade, document keeps existing
    db_session.flush()
    db_session.refresh(d)
    assert d.connector_id is None


@pytest.mark.parametrize("kw", [{"kind": "ftp"}, {"visibility": "shared"}])
def test_connector_checks(db_session, user, kw):
    db_session.add(_connector(user, **kw))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_run_status_check_and_unique_name(db_session, user):
    c = _connector(user)
    db_session.add(c)
    db_session.flush()
    db_session.add(ConnectorRun(connector_id=c.id, status="weird"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
    db_session.add_all([_connector(user), _connector(user)])
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_migration_down_and_up(session_factory, db_session):
    db_session.rollback()
    cfg = _alembic_config()
    command.downgrade(cfg, "0007")
    try:
        with session_factory() as s:
            insp = inspect(s.connection())
            assert not TABLES & set(insp.get_table_names())
            assert "connector_id" not in {c["name"] for c in insp.get_columns("documents")}
    finally:
        command.upgrade(cfg, "head")
    with session_factory() as s:
        assert TABLES <= set(inspect(s.connection()).get_table_names())
        assert s.scalar(text("SELECT count(*) FROM notifications")) == 0
