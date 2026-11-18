import pytest
from alembic.script import ScriptDirectory
from conftest import _alembic_config
from sqlalchemy import text

from roracle.models import User
from roracle.models_saved import SavedSearch

U = "/me/saved-searches"
NF = {"detail": "not found"}


def _mk(client, h, name="a", query="q=x&sort=newest"):
    return client.post(U, json={"name": name, "query": query}, headers=h)


def test_crud_round_trip(client, as_ana):
    r = _mk(client, as_ana, "  Spectral  ")
    assert r.status_code == 201
    s = r.json()
    assert set(s) == {"id", "name", "query", "created_at", "updated_at"}
    assert s["name"] == "Spectral"
    other = _mk(client, as_ana, "Other").json()
    r = client.patch(f"{U}/{s['id']}", json={"query": "q=y"}, headers=as_ana)
    assert r.status_code == 200 and r.json()["query"] == "q=y" and r.json()["name"] == "Spectral"
    ids = [x["id"] for x in client.get(U, headers=as_ana).json()]
    assert ids == [s["id"], other["id"]]  # updated_at desc
    assert client.delete(f"{U}/{s['id']}", headers=as_ana).status_code == 204
    assert [x["id"] for x in client.get(U, headers=as_ana).json()] == [other["id"]]


def test_other_user_is_404(client, as_ana, as_bob):
    sid = _mk(client, as_ana).json()["id"]
    unknown = "00000000-0000-0000-0000-000000000001"
    assert client.get(U, headers=as_bob).json() == []
    for sid_ in (sid, unknown):
        for r in (
            client.patch(f"{U}/{sid_}", json={"name": "z"}, headers=as_bob),
            client.delete(f"{U}/{sid_}", headers=as_bob),
        ):
            assert r.status_code == 404 and r.json() == NF
    assert len(client.get(U, headers=as_ana).json()) == 1


def test_duplicate_name_409(client, as_ana, as_bob):
    _mk(client, as_ana, "dup")
    r = _mk(client, as_ana, "dup")
    assert r.status_code == 409 and r.json() == {"detail": "a saved search with this name exists"}
    assert _mk(client, as_bob, "dup").status_code == 201  # per owner
    b = _mk(client, as_ana, "other").json()
    r = client.patch(f"{U}/{b['id']}", json={"name": "dup"}, headers=as_ana)
    assert r.status_code == 409
    assert client.get(U, headers=as_ana).status_code == 200  # session still usable


def test_limit_201st_422(client, as_ana, db_session):
    uid = db_session.scalar(text("SELECT 1"))  # keep session warm
    assert _mk(client, as_ana, "n0").status_code == 201
    owner = db_session.scalar(SavedSearch.__table__.select().with_only_columns(SavedSearch.owner_id))
    db_session.add_all(SavedSearch(owner_id=owner, name=f"x{i}", query="") for i in range(1, 200))
    db_session.flush()
    r = _mk(client, as_ana, "one-too-many")
    assert r.status_code == 422 and r.json() == {"detail": "at most 200 saved searches"}
    assert uid == 1


@pytest.mark.parametrize(
    "body",
    [
        {"name": "n", "query": "?q=x"},
        {"name": "n", "query": "q=x\nq=y"},
        {"name": "n", "query": "a" * 4001},
        {"name": "   ", "query": "q"},
        {"name": "x" * 101, "query": "q"},
    ],
)
def test_validation_422(client, as_ana, body):
    assert client.post(U, json=body, headers=as_ana).status_code == 422


def test_patch_validation_422(client, as_ana):
    sid = _mk(client, as_ana).json()["id"]
    assert client.patch(f"{U}/{sid}", json={"query": "?x"}, headers=as_ana).status_code == 422


def test_user_delete_cascades(client, as_ana, db_session):
    _mk(client, as_ana)
    owner = db_session.scalar(SavedSearch.__table__.select().with_only_columns(SavedSearch.owner_id))
    db_session.execute(text("DELETE FROM users WHERE id = :i"), {"i": owner})
    assert db_session.scalar(text("SELECT count(*) FROM saved_searches")) == 0
    assert db_session.get(User, owner) is None


def test_single_migration_head():
    assert len(ScriptDirectory.from_config(_alembic_config()).get_heads()) == 1
