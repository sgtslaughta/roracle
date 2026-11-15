import os

import pytest
from sqlalchemy import select

from roracle.connectors import RunDeadline, fs
from roracle.connectors.run import handle_connector_run
from roracle.models import Connector, ConnectorItem, ConnectorRun, Document, User


def keys(cfg_path, roots, include=None):
    cfg = {"path": str(cfg_path), **({"include": include} if include else {})}
    return sorted(i.key for i in fs.items(cfg, [str(r) for r in roots]))


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "sub").mkdir(parents=True)
    (root / ".hidden").mkdir()
    outside.mkdir()
    (root / "a.txt").write_text("a")
    (root / "sub" / "b.pdf").write_text("b")
    (root / ".dot.txt").write_text("dot")
    (root / ".hidden" / "h.txt").write_text("h")
    (outside / "secret.txt").write_text("secret")
    return root, outside


def test_walks_regular_files_skipping_dotfiles_and_dotdirs(tree):
    root, _ = tree
    assert keys(root, [root]) == ["a.txt", "sub/b.pdf"]
    assert keys(root, [root], ["*.pdf"]) == ["sub/b.pdf"]


def test_file_symlink_out_is_skipped(tree):
    root, outside = tree
    os.symlink(outside / "secret.txt", root / "link.txt")
    assert "link.txt" not in keys(root, [root])


def test_dir_symlink_out_is_not_followed(tree):
    root, outside = tree
    os.symlink(outside, root / "escape")
    got = keys(root, [root])
    assert not any("secret" in k or k.startswith("escape") for k in got)


def test_dir_symlink_inside_not_followed_either(tree):
    root, _ = tree
    os.symlink(root / "sub", root / "alias")
    assert keys(root, [root]) == ["a.txt", "sub/b.pdf"]


def test_fifo_is_skipped_without_blocking(tree):
    root, _ = tree
    os.mkfifo(root / "pipe")
    assert "pipe" not in keys(root, [root])


@pytest.mark.parametrize("how", ["outside", "dotdot", "root_symlink", "prefix_sibling", "relative", "none"])
def test_path_outside_roots_rejected(tree, tmp_path, how):
    root, outside = tree
    sibling = tmp_path / "root-evil"
    sibling.mkdir()
    link = tmp_path / "root" / "lnk"
    os.symlink(outside, link)
    path = {
        "outside": outside,
        "dotdot": root / ".." / "outside",
        "root_symlink": link,
        "prefix_sibling": sibling,
        "relative": "root",
        "none": root,
    }[how]
    roots = [] if how == "none" else [root]
    with pytest.raises(ValueError):
        fs.validate_path(str(path), [str(r) for r in roots])


def test_symlinked_root_config_resolves_and_is_allowed_only_if_target_inside(tree, tmp_path):
    root, _ = tree
    link = tmp_path / "inroot"
    os.symlink(root / "sub", link)  # link outside root dir, target inside
    assert fs.validate_path(str(link), [str(root)])[0] == str((root / "sub").resolve())


def test_dotdot_that_stays_inside_is_ok(tree):
    root, _ = tree
    assert fs.validate_path(str(root / "sub" / ".." / "sub"), [str(root)])[0] == str((root / "sub").resolve())


def _connector(db, **kw):
    u = User(sub="adm", username="adm")
    db.add(u)
    db.flush()
    c = Connector(
        name="c", kind="fs", owner_id=u.id, visibility="everyone", config=kw.get("config", {}), org_unit=""
    )
    db.add(c)
    db.commit()
    return c


def _run(db, sf, settings, store, c):
    handle_connector_run(db, {"connector_id": str(c.id)}, settings=settings, store=store, session_factory=sf)
    db.rollback()
    return db.scalars(select(ConnectorRun).order_by(ConnectorRun.id.desc())).first()


def test_run_ingests_only_inside_and_fails_if_config_moved_outside(
    tree, db_session, session_factory, settings, store
):
    root, outside = tree
    os.symlink(outside / "secret.txt", root / "link.txt")
    os.symlink(outside, root / "escape")
    s = settings.model_copy(update={"connector_fs_roots": [str(root)]})
    c = _connector(db_session, config={"path": str(root), "include": ["*"]})
    run = _run(db_session, session_factory, s, store, c)
    assert run.status == "ok" and run.stats["ingested"] == 2
    assert sorted(db_session.scalars(select(ConnectorItem.key))) == ["a.txt", "sub/b.pdf"]
    assert {d.connector_id for d in db_session.scalars(select(Document))} == {c.id}
    # config edited directly in the DB to a path outside the roots: the run fails closed
    c.config = {"path": str(outside), "include": ["*"]}
    db_session.commit()
    run = _run(db_session, session_factory, s, store, c)
    assert run.status == "failed" and "outside" in run.log
    assert db_session.scalar(select(ConnectorItem).where(ConnectorItem.key == "secret.txt")) is None


def test_symlink_swapped_in_after_validation_does_not_escape(tree, monkeypatch):
    root, outside = tree
    real = fs.validate_path

    def swap_after_check(path, roots):
        out = real(path, roots)
        (root / "sub" / "b.pdf").unlink()
        (root / "sub").rmdir()
        os.symlink(outside, root / "sub")  # attacker swaps the validated directory for a symlink
        return out

    monkeypatch.setattr(fs, "validate_path", swap_after_check)
    with pytest.raises(OSError):
        list(fs.items({"path": str(root / "sub")}, [str(root)]))


def test_hardlink_inside_walk_is_regular_file(tree):
    root, outside = tree
    os.link(outside / "secret.txt", root / "hard.txt")  # documented limitation: not distinguishable
    assert "hard.txt" in keys(root, [root])


def test_expired_deadline_stops_walk(tree):
    root, _ = tree
    with pytest.raises(RunDeadline):
        list(fs.items({"path": str(root)}, [str(root)], deadline=0))
