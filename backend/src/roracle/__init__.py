import os
from collections.abc import Callable, Mapping
from importlib import metadata
from pathlib import Path


def _dist_version() -> str:
    return metadata.version("roracle")


def resolve_version(
    env: Mapping[str, str], version_file: Path, dist_version: Callable[[], str] = _dist_version
) -> str:
    # ROR_VERSION > repo VERSION file > installed metadata > dev. Containers set ROR_VERSION (Task 8).
    if v := env.get("ROR_VERSION"):
        return v
    if version_file.exists():
        return version_file.read_text().strip()
    try:
        return dist_version()
    except metadata.PackageNotFoundError:
        return "0.0.0+dev"


__version__ = resolve_version(os.environ, Path(__file__).resolve().parents[3] / "VERSION")
