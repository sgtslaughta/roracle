"""Bounded, read-only inspection of zip / tar / tar.gz / gz archives, shared by the upload path, the
ingest pipeline and the connectors. stdlib only; members are streamed, never written to disk by name."""

import gzip
import os
import re
import stat
import struct
import tarfile
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Self

from roracle.storage import TooLarge

MAX_MEMBERS = 500
MAX_ENTRIES = 4 * MAX_MEMBERS  # every entry seen counts, accepted or not
MAX_REPORTED = 50  # skipped reasons kept (and warned); the rest are only counted
TOTAL_FACTOR = 10  # total uncompressed bytes <= TOTAL_FACTOR x per-file cap
_CHUNK = 1 << 20


class ArchiveError(Exception):
    """Unreadable archive, or one that unpacks beyond the limits."""


def kind_of(filename: str) -> str | None:
    n = filename.lower()
    if n.endswith(".zip"):
        return "zip"
    if n.endswith((".tar.gz", ".tgz", ".tar")):
        return "tar"
    if n.endswith(".gz"):
        return "gz"
    return None


def stem_of(filename: str) -> str:
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    for ext in (".tar.gz", ".tgz", ".tar", ".zip", ".gz"):
        if base.lower().endswith(ext):
            return base[: -len(ext)] or "archive"
    return base or "archive"


@dataclass(frozen=True)
class Member:
    name: str  # normalised relative posix path
    size: int | None  # header size (not trusted; reads are counted); None for a bare .gz
    token: str  # zip CRC / tar mtime: changes when the member changes
    ref: Any = None

    @property
    def basename(self) -> str:
        return self.name.rsplit("/", 1)[-1]


class _Capped:
    """File wrapper whose furthest position may not pass `limit` (stops gzip/tar bombs while listing)."""

    def __init__(self, raw: BinaryIO, limit: int) -> None:
        self.raw, self.limit = raw, limit

    def read(self, n: int = -1) -> bytes:
        room = self.limit + 1 - self.raw.tell()
        data = self.raw.read(room if n is None or n < 0 else min(n, room))
        self._check()
        return data

    def seek(self, off: int, whence: int = 0) -> int:
        if whence == 0 and off > self.limit:
            raise ArchiveError("archive unpacks to more than the allowed total (uncompressed)")
        r = self.raw.seek(off, whence)
        self._check()
        return r

    def tell(self) -> int:
        return self.raw.tell()

    def _check(self) -> None:
        if self.raw.tell() > self.limit:
            raise ArchiveError("archive unpacks to more than the allowed total (uncompressed)")


class _Counted:
    """Member stream: counts real bytes against the member cap and the archive's total budget."""

    def __init__(self, raw: BinaryIO, a: "Archive") -> None:
        self.raw, self.a, self.n = raw, a, 0

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            parts = []
            while chunk := self.read(_CHUNK):
                parts.append(chunk)
            return b"".join(parts)
        data = self.raw.read(min(n, self.a.cap + 1 - self.n))
        self.n += len(data)
        self.a.total += len(data)
        if self.n > self.a.cap:
            raise TooLarge(f"archive member exceeds {self.a.cap} bytes")
        if self.a.total > self.a.limit:
            raise ArchiveError("archive unpacks to more than the allowed total (uncompressed)")
        return data

    def close(self) -> None:
        pass  # the archive owns the underlying handles


def _clean(name: str) -> str | None:
    """Normalised path, None when unsafe (absolute, `..`, drive letter, NUL)."""
    n = name.replace("\\", "/")
    if "\0" in n or n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        return None
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def _junk(path: str) -> bool:
    return any(p.startswith(".") or p == "__MACOSX" for p in path.split("/"))


def _zip_entry_count(path: Path) -> int:
    """Total entries from the end-of-central-directory record (zip64 aware), read without parsing the
    directory, so a huge entry list is refused before zipfile allocates anything for it."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        start = max(0, size - 65557)
        f.seek(start)
        tail = f.read()
        i = tail.rfind(b"PK\x05\x06")
        if i < 0 or len(tail) < i + 22:
            raise ArchiveError("unreadable archive")
        count = struct.unpack("<H", tail[i + 10 : i + 12])[0]
        if count == 0xFFFF or tail[max(0, i - 20) : max(0, i - 20) + 4] == b"PK\x06\x07":
            loc = tail[i - 20 : i] if i >= 20 else b""
            if loc[:4] != b"PK\x06\x07":
                return count
            off = struct.unpack("<Q", loc[8:16])[0]
            f.seek(off)
            rec = f.read(40)
            if len(rec) < 40 or rec[:4] != b"PK\x06\x06":
                raise ArchiveError("unreadable archive")
            count = struct.unpack("<Q", rec[32:40])[0]
        return count


class Archive:
    """Open `path` as `filename`'s kind. `.members` are the safe regular files (at most 500), `.skipped` the
    (name, reason) pairs refused; `warn` receives each reason too."""

    def __init__(self, path: Path, filename: str, cap: int, warn: Callable[[str], None] | None = None):
        self.kind = kind_of(filename)
        self.stem = stem_of(filename)
        self.cap, self.limit = cap, cap * TOTAL_FACTOR
        self.total = 0
        self.skipped: list[tuple[str, str]] = []
        self._more = 0
        self.members: list[Member] = []
        self._warn = warn or (lambda m: None)
        self._zip: zipfile.ZipFile | None = None
        self._tar: tarfile.TarFile | None = None
        self._single: _Capped | None = None
        self._files: list[BinaryIO] = []
        if self.kind is None:
            raise ArchiveError("not a supported archive type")
        try:
            self._open(path)
            self.members = self._scan()
        except ArchiveError:
            self.close()
            raise
        except (
            zipfile.BadZipFile,
            tarfile.TarError,
            OSError,
            EOFError,
            zlib.error,
            NotImplementedError,
        ) as e:
            self.close()
            raise ArchiveError(f"unreadable archive: {type(e).__name__}") from None

    def _open(self, path: Path) -> None:
        if self.kind == "zip":
            if _zip_entry_count(path) > MAX_ENTRIES:
                raise ArchiveError("archive has too many entries")
            self._zip = zipfile.ZipFile(path)
            return
        raw: BinaryIO = open(path, "rb")  # noqa: SIM115 - closed in close()
        self._files.append(raw)
        if raw.read(2) == b"\x1f\x8b":
            raw.seek(0)
            raw = gzip.GzipFile(fileobj=raw)
            self._files.append(raw)
        elif self.kind == "gz":
            raise ArchiveError("not gzip data")
        else:
            raw.seek(0)
        capped = _Capped(raw, self.limit)
        try:
            self._tar = tarfile.open(fileobj=capped, mode="r:")  # type: ignore[call-overload]  # noqa: SIM115 - closed in close()
        except tarfile.ReadError:
            if self.kind != "gz":
                raise ArchiveError("not a tar archive") from None
            capped.seek(0)
            self._single = capped

    def _note(self, name: str, reason: str) -> None:
        self.skipped.append((name, reason))
        self._warn(f"{name}: {reason}" if name != "…" else f"… {reason}")

    def _skip(self, name: str, reason: str) -> None:
        if len(self.skipped) < MAX_REPORTED:
            self._note(name, reason)
        else:
            self._more += 1

    def _entries(self):
        """(raw name, size, token, kind, ref) per entry; kind is dir, file or other."""
        if self._zip is not None:
            for zi in self._zip.infolist():
                mode = zi.external_attr >> 16
                regular = stat.S_IFMT(mode) in (0, stat.S_IFREG)
                kind = "dir" if zi.is_dir() else "file" if regular else "other"
                yield zi.filename, zi.file_size, f"{zi.CRC:08x}", kind, zi
        elif self._tar is not None:
            while (ti := self._tar.next()) is not None:  # header by header: never getmembers()
                kind = (
                    "dir"
                    if ti.isdir()
                    else "file"
                    if ti.type in (tarfile.REGTYPE, tarfile.AREGTYPE)
                    else "other"
                )
                yield ti.name, ti.size, str(int(ti.mtime)), kind, ti
        else:
            yield self.stem, None, "gz", "file", None

    def _scan(self) -> list[Member]:
        if self._zip is not None and len(self._zip.infolist()) > MAX_ENTRIES:
            raise ArchiveError("archive has too many entries")
        out: list[Member] = []
        seen: set[str] = set()
        for n, (raw, size, token, kind, ref) in enumerate(self._entries()):
            if n >= MAX_ENTRIES:
                if not out:
                    raise ArchiveError("archive has too many entries")
                self._note("…", f"archive has more than {MAX_ENTRIES} entries; the rest were ignored")
                break
            name = _clean(raw)
            if kind == "dir":
                continue
            if name is None:
                self._skip(raw, "unsafe path")
            elif _junk(name):
                continue
            elif kind == "other":
                self._skip(name, "not a regular file")
            elif self._zip is not None and ref.flag_bits & 1:
                self._skip(name, "encrypted")
            elif kind_of(name) is not None and self.kind != "gz":
                self._skip(name, "nested archive is not unpacked")
            elif name not in seen:
                if len(out) >= MAX_MEMBERS:
                    self._note("…", f"more than {MAX_MEMBERS} files in the archive; the rest are skipped")
                    break
                seen.add(name)
                out.append(Member(name, size, token, ref))
        if self._more:
            self._note("…", f"and {self._more} more skipped")
        return out

    def open(self, m: Member) -> _Counted:
        if self._zip is not None:
            raw: BinaryIO = self._zip.open(m.ref)
        elif self._tar is not None:
            f = self._tar.extractfile(m.ref)
            if f is None:
                raise ArchiveError(f"{m.name}: not readable")
            raw = f
        else:
            assert self._single is not None
            self._single.seek(0)
            raw = self._single  # type: ignore[assignment]
        return _Counted(raw, self)

    def read(self, m: Member) -> bytes:
        return self.open(m).read()

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
        if self._tar is not None:
            self._tar.close()
        for f in reversed(self._files):
            f.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def spool(stream: BinaryIO, cap: int) -> Path:
    """Copy `stream` to a temp file (caller unlinks); TooLarge past `cap`."""
    import tempfile

    fd, name = tempfile.mkstemp(prefix="roracle-arc-")
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while data := stream.read(_CHUNK):
                size += len(data)
                if size > cap:
                    raise TooLarge(f"archive exceeds {cap} bytes")
                out.write(data)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)
