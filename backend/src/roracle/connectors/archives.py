"""Unpack archive items into one Item per member file (all connector kinds).

LaTeX source archives pass through as one item (the ingest pipeline turns them into one paper). Limits and
safety rules live in `ingest/archive.py`; nothing is extracted to disk by member name.
"""

import mimetypes
import os
from collections.abc import Callable, Iterator
from pathlib import Path

from roracle.connectors import Item
from roracle.ingest.archive import Archive, ArchiveError, Member, kind_of, spool
from roracle.ingest.formats import latex_main
from roracle.storage import TooLarge


def expand(items: Iterator[Item], cap: int, warn: Callable[[str], None]) -> Iterator[Item]:
    try:
        for it in items:
            if kind_of(it.filename) is None or (it.size is not None and it.size > cap):
                yield it  # not an archive, or too big (the run counts it too_large)
                continue
            yield from _expand_one(it, cap, warn)
    finally:
        getattr(items, "close", lambda: None)()  # the source's own cleanup (e.g. the HTTP pool)


def _expand_one(it: Item, cap: int, warn: Callable[[str], None]) -> Iterator[Item]:
    stream = it.open()
    try:
        path = spool(stream, cap)
    except TooLarge:
        yield it
        return
    finally:
        stream.close()
    try:
        try:
            a = Archive(path, it.filename, cap, lambda m: warn(f"{it.key}: {m}"))
        except ArchiveError as e:
            warn(f"{it.key}: {e}")
            return
        try:
            if _is_latex(a, it, warn):
                yield Item(it.key, it.etag, it.filename, it.content_type, os.path.getsize(path),
                           lambda: open(path, "rb"))  # fmt: skip  # noqa: SIM115 - caller closes
                return
            for m in a.members:
                yield _member_item(a, it, m)
        finally:
            a.close()
    finally:
        Path(path).unlink(missing_ok=True)


def _is_latex(a: Archive, it: Item, warn: Callable[[str], None]) -> bool:
    try:
        return latex_main(a) is not None
    except ArchiveError as e:
        warn(f"{it.key}: {e}")
        return False


def _member_item(a: Archive, it: Item, m: Member) -> Item:
    return Item(
        key=f"{it.key}!{m.name}",
        etag=f"{it.etag}!{m.size}-{m.token}",
        filename=m.basename,
        content_type=mimetypes.guess_type(m.basename)[0] or "application/octet-stream",
        size=m.size,
        open=lambda: a.open(m),  # type: ignore[arg-type,return-value]
    )
