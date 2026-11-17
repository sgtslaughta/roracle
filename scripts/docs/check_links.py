"""Check relative links and #anchors in the repo Markdown (GitLab slug rules). http(s) links are only syntax-checked.

Run: python scripts/docs/check_links.py   (exit 1 with file:line on any failure)
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mdutil import ROOT, lines, md_files

INLINE = re.compile(r"(?<!\\)!?\[(?:[^\]\\]|\\.)*\]\(\s*(<[^>]*>|[^)\s]*)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
REFDEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
HTML_ID = re.compile(r"""<a\s+[^>]*(?:name|id)=["']([^"']+)["']""")
CODE_SPAN = re.compile(r"(`+)(.+?)\1")


def slug(heading: str) -> str:
    """GitLab heading anchor: strip markup, lowercase, drop punctuation, spaces to hyphens, collapse hyphens."""
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    t = CODE_SPAN.sub(r"\2", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"[*~]", "", t).lower()
    t = "".join(c for c in t if c in " -_" or unicodedata.category(c)[0] in "LN" or c == "‍")
    return re.sub(r"-{2,}", "-", t.strip().replace(" ", "-"))


def anchors(text: str) -> set[str]:
    out: set[str] = set()
    seen: dict[str, int] = {}
    for _, line, fence in lines(text):
        if fence is not None:
            continue
        out.update(HTML_ID.findall(line))
        m = HEADING.match(line)
        if m:
            s = slug(m.group(2))
            n = seen.get(s, 0)
            seen[s] = n + 1
            out.add(s if n == 0 else f"{s}-{n}")
    return out


def targets(text: str) -> list[tuple[int, str]]:
    """(lineno, raw target) for every inline/reference link outside code."""
    out = []
    for n, line, fence in lines(text):
        if fence is not None:
            continue
        line = CODE_SPAN.sub("", line)
        for m in INLINE.finditer(line):
            out.append((n, m.group(1)))
        m = REFDEF.match(line)
        if m:
            out.append((n, m.group(1)))
    return out


def check_target(src: Path, raw: str, root: Path, cache: dict[Path, set[str]]) -> str | None:
    """Return an error message, or None when the link is fine."""
    t = raw.strip("<>")
    if not t:
        return "empty link target"
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", t):
        if t.lower().startswith(("http://", "https://")):
            u = urlsplit(t)
            return None if u.netloc and " " not in t else f"malformed URL {t!r}"
        return None  # mailto:, etc.
    path, _, frag = t.partition("#")
    path = unquote(path.split("?", 1)[0])
    if path:
        dest = (root / path.lstrip("/")) if path.startswith("/") else (src.parent / path)
        dest = Path(*dest.resolve().parts)
        if not dest.exists():
            return f"broken link {t!r} (no such file)"
    else:
        dest = src
    if frag and dest.is_file() and dest.suffix == ".md":
        if dest not in cache:
            cache[dest] = anchors(dest.read_text())
        f = unquote(frag).lower()
        if f not in cache[dest] and not re.fullmatch(r"l\d+(-l?\d+)?", f):
            return f"broken anchor {t!r} (no heading slug {f!r} in {dest.relative_to(root)})"
    return None


def check(root: Path = ROOT, files: list[Path] | None = None) -> list[str]:
    errs: list[str] = []
    cache: dict[Path, set[str]] = {}
    for f in files if files is not None else md_files(root):
        for n, raw in targets(f.read_text()):
            e = check_target(f, raw, root, cache)
            if e:
                errs.append(f"{f.relative_to(root)}:{n}: {e}")
    return errs


def main() -> int:
    errs = check()
    print("\n".join(errs))
    print(f"check_links: {len(errs)} problem(s)", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
