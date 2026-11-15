from importlib.metadata import PackageNotFoundError

import pytest

from roracle import resolve_version


def _missing():
    raise PackageNotFoundError


def test_env_wins(tmp_path):
    f = tmp_path / "VERSION"
    f.write_text("1.0.0\n")
    assert resolve_version({"ROR_VERSION": "9.9.9"}, f, lambda: "2.0.0") == "9.9.9"


def test_version_file_used(tmp_path):
    f = tmp_path / "VERSION"
    f.write_text("1.0.0\n")
    assert resolve_version({}, f, lambda: "2.0.0") == "1.0.0"


def test_installed_metadata_used(tmp_path):
    assert resolve_version({}, tmp_path / "VERSION", lambda: "2.0.0") == "2.0.0"


def test_dev_fallback(tmp_path):
    assert resolve_version({}, tmp_path / "VERSION", _missing) == "0.0.0+dev"


@pytest.mark.parametrize("env", [{}, {"ROR_VERSION": ""}])
def test_empty_env_ignored(tmp_path, env):
    f = tmp_path / "VERSION"
    f.write_text("1.0.0")
    assert resolve_version(env, f, _missing) == "1.0.0"
