import pytest

from roracle import db


class Eng:
    def __init__(self, fails):
        self.fails = fails
        self.calls = 0

    def connect(self):
        self.calls += 1
        if self.calls <= self.fails:
            raise OSError("down")

        class C:
            def close(self):
                pass

        return C()


def test_wait_retries_then_succeeds(monkeypatch):
    eng = Eng(2)
    sleeps = []
    monkeypatch.setattr(db, "create_engine", lambda url: eng)
    db.wait_for_db("postgresql+psycopg://u:p@h/d", 60, sleep=sleeps.append)
    assert eng.calls == 3 and sleeps == [5, 5]


def test_wait_fails_fast_when_deadline_passes(monkeypatch):
    eng = Eng(10**6)
    monkeypatch.setattr(db, "create_engine", lambda url: eng)
    with pytest.raises(SystemExit) as e:
        db.wait_for_db("postgresql+psycopg://u:p@h/d", 0, sleep=lambda s: None)
    assert "not reachable" in str(e.value) and eng.calls == 1


def test_wait_message_hides_password(monkeypatch):
    monkeypatch.setattr(db, "create_engine", lambda url: Eng(10**6))
    with pytest.raises(SystemExit) as e:
        db.wait_for_db("postgresql+psycopg://u:sekret@h/d", 0, sleep=lambda s: None)
    assert "sekret" not in str(e.value)


def test_migrate_wait_flag(monkeypatch):
    import roracle.__main__ as m

    seen = {}
    monkeypatch.setattr(db, "wait_for_db", lambda url, secs, **kw: seen.update(secs=secs))
    monkeypatch.setattr("alembic.command.upgrade", lambda cfg, rev: seen.update(rev=rev))
    m.main(["migrate", "--wait", "7"])
    assert seen == {"secs": 7, "rev": "head"}
