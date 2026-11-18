"""Pre-conversion before docling: formats it cannot read are rewritten to ones it can.

TSV -> CSV, JSON -> Markdown, .nxml -> .xml, LaTeX source archives -> one inlined .tex. Any other archive
must have been unpacked at intake (upload / connector); one reaching the pipeline is refused.
"""

import csv
import io
import json
import posixpath
import re
import tempfile
from pathlib import Path

from roracle.ingest.archive import Archive, ArchiveError, Member, kind_of
from roracle.queue import NonRetryable
from roracle.storage import TooLarge

MAX_INCLUDE_DEPTH = 10
_INCLUDE = re.compile(r"\\(?:input|include)\s*\{([^}\n]+)\}")
_PREFERRED_MAIN = ("main.tex", "ms.tex")


def prepare(data: bytes, filename: str, cap: int) -> tuple[bytes, str]:
    """(data, filename) to send to docling."""
    low = filename.lower()
    stem = filename.rsplit(".", 1)[0]
    if low.endswith(".tsv"):
        return _tsv_to_csv(data), f"{stem}.csv"
    if low.endswith(".json"):
        return _json_to_md(data, filename), f"{stem}.md"
    if low.endswith(".nxml"):
        return data, f"{stem}.xml"
    if kind_of(filename):
        return _latex_from_archive(data, filename, cap)
    return data, filename


def _tsv_to_csv(data: bytes) -> bytes:
    out = io.StringIO()
    try:
        csv.writer(out, lineterminator="\n").writerows(
            csv.reader(io.StringIO(data.decode("utf-8-sig", "replace"), newline=""), delimiter="\t")
        )
    except csv.Error:
        raise NonRetryable("invalid TSV") from None
    return out.getvalue().encode()


def _json_to_md(data: bytes, filename: str) -> bytes:
    try:
        body = json.dumps(json.loads(data.decode("utf-8-sig")), indent=2, ensure_ascii=False)
    except (ValueError, RecursionError):
        raise NonRetryable("invalid JSON") from None
    longest = max((len(m) for m in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"# {filename}\n\n{fence}json\n{body}\n{fence}\n".encode()


def _latex_from_archive(data: bytes, filename: str, cap: int) -> tuple[bytes, str]:
    with tempfile.NamedTemporaryFile(prefix="roracle-arc-") as f:
        f.write(data)
        f.flush()
        try:
            with Archive(Path(f.name), filename, cap) as a:
                main = latex_main(a)
                if main is None:
                    raise NonRetryable("archive was not unpacked")
                return flatten_latex(a, main).encode(), f"{a.stem}.tex"
        except (ArchiveError, TooLarge) as e:
            raise NonRetryable(f"archive could not be read: {e}") from None


def latex_main(a: Archive) -> Member | None:
    """The main TeX file: a `.tex` member (any single member of a bare .gz) holding `\\documentclass`."""
    found: list[Member] = []
    for m in a.members:
        if (a.kind == "gz" or m.name.lower().endswith(".tex")) and (m.size is None or m.size <= a.cap):
            try:
                if b"\\documentclass" in a.read(m):
                    found.append(m)
            except TooLarge:
                continue
    if len(found) > 1:
        pref = [m for m in found if m.basename.lower() in _PREFERRED_MAIN]
        found = pref or sorted(found, key=lambda m: m.size or 0, reverse=True)
    return found[0] if found else None


def flatten_latex(a: Archive, main: Member) -> str:
    by_name = {m.name: m for m in a.members}

    def text(m: Member) -> str:
        return a.read(m).decode("utf-8", "replace")

    def find(ref: str, base: str) -> Member | None:
        ref = ref.strip()
        for cand in (posixpath.normpath(posixpath.join(base, ref)), posixpath.normpath(ref)):
            for name in (cand, f"{cand}.tex"):
                if name in by_name:
                    return by_name[name]
        return None

    def inline(src: str, base: str, depth: int) -> str:
        if depth >= MAX_INCLUDE_DEPTH:
            return src

        def sub(mo: re.Match) -> str:
            m = find(mo.group(1), base)
            if m is None:
                return mo.group(0)
            try:
                return inline(text(m), posixpath.dirname(m.name), depth + 1)
            except TooLarge:
                return mo.group(0)

        return _INCLUDE.sub(sub, src)

    return inline(text(main), posixpath.dirname(main.name), 0)
