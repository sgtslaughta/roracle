import argparse
import uuid
from contextlib import contextmanager

import pytest

from roracle import runtime
from roracle.eval import cli, runner
from roracle.models import User
from roracle.models_eval import EvalQuery, EvalSet


def args(**kw):
    return argparse.Namespace(
        **{
            "set_name": "s",
            "fixture": None,
            "path": "chat",
            "label": "",
            "set_override": [],
            "index": "",
            "json_out": None,
        }
        | kw
    )


@pytest.fixture
def patched(monkeypatch, db_session, settings):
    @contextmanager
    def sf():
        yield db_session

    monkeypatch.setattr(cli, "make_session_factory", lambda url: sf)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli.clients, "es", lambda s: None)
    monkeypatch.setattr(cli.clients, "llm", lambda s: None)
    return db_session


def make_set(db, admin_only=False):
    u = User(id=uuid.uuid4(), sub="u", username="u", is_admin=admin_only)
    s = EvalSet(name="s")
    db.add_all([u, s])
    db.flush()
    db.add(
        EvalQuery(
            set_id=s.id,
            key="q",
            text="t",
            kind="fact",
            principals=[u.id],
            relevant=[{"doc": str(uuid.uuid4())}],
        )
    )
    db.flush()


def test_fixture_not_implemented_exit_2(patched, capsys):
    assert cli.run(args(set_name=None, fixture="x.yaml")) == 2
    assert "not implemented" in capsys.readouterr().err


def test_unknown_set_and_r5_exit_2(patched):
    assert cli.run(args(set_name="nope")) == 2
    make_set(patched, admin_only=True)
    assert cli.run(args()) == 2


def test_run_error_exit_1(patched, monkeypatch):
    make_set(patched)
    monkeypatch.setattr(runner, "run_set", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert cli.run(args()) == 1


def test_uses_cutover_embedding(patched, monkeypatch):
    make_set(patched)
    runtime.set_active_embedding(patched, "cut-model", 4, "chunks-cut")
    seen = {}

    def fake(session, es, llm, settings, gs, **k):
        seen["s"] = settings
        raise RuntimeError("stop")

    monkeypatch.setattr(runner, "run_set", fake)
    cli.run(args())
    assert (seen["s"].embed_model, seen["s"].embed_index) == ("cut-model", "chunks-cut")
