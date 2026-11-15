import os
from pathlib import Path


def _read_version() -> str:
    if env := os.environ.get("ROR_VERSION"):
        return env
    f = Path(__file__).resolve().parents[3] / "VERSION"
    return f.read_text().strip() if f.exists() else "0.0.0+dev"


__version__ = _read_version()
