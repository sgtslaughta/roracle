"""Shared helpers for the doc checkers: file discovery, fence-aware line iteration, allow-list."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FENCE = re.compile(r"^\s{0,3}(```+|~~~+)\s*([\w-]*)")
SKIP_PARTS = {"node_modules", ".git", ".venv", ".worktrees"}


def md_files(root: Path = ROOT) -> list[Path]:
    """Every Markdown file the checks cover (docs/, root + component READMEs, deploy READMEs)."""
    found = set(root.glob("docs/**/*.md"))
    for name in ("README.md", "backend/README.md", "frontend/README.md"):
        if (root / name).is_file():
            found.add(root / name)
    found.update(root.glob("deploy/**/README.md"))
    return sorted(p for p in found if not SKIP_PARTS & set(p.relative_to(root).parts))


def lines(text: str) -> Iterator[tuple[int, str, str | None]]:
    """Yield (lineno, line, fence_lang); fence_lang is None outside fenced code, '' for an untagged fence.

    The fence delimiter lines themselves are not yielded.
    """
    fence: str | None = None
    lang: str | None = None
    for n, line in enumerate(text.splitlines(), 1):
        m = FENCE.match(line)
        if m:
            if fence is None:
                fence, lang = m.group(1)[0], m.group(2).lower()
            elif m.group(1)[0] == fence and not m.group(2):
                fence = lang = None
            continue
        yield n, line, lang if fence else None


def load_allow(path: Path) -> set[tuple[str | None, str]]:
    """Allow-list lines: `token` (any file) or `relative/file.md|token`. `#` starts a comment."""
    out: set[tuple[str | None, str]] = set()
    if not path.is_file():
        return out
    for raw in path.read_text().splitlines():
        s = raw.split("#", 1)[0].strip()
        if not s:
            continue
        if "|" in s:
            f, t = s.split("|", 1)
            out.add((f.strip(), t.strip()))
        else:
            out.add((None, s))
    return out
