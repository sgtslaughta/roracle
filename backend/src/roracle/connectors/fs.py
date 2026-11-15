"""Filesystem source. Cannot leave its allowlisted roots.

Guarantees: the configured path's realpath must be a root or below one (checked at create and every
run); the walk never follows directory symlinks (os.fwalk follow_symlinks=False); every file is opened
relative to its directory fd with O_NOFOLLOW and must be a regular file (fstat on the opened fd, so
no check/use gap). FIFOs/devices are never read (O_NONBLOCK keeps the open from blocking).
Residual: a writer who can swap a *parent* of the root between the realpath check and the walk's
first open could redirect it; mount the roots read-only (deployment guide).
"""

import errno
import fnmatch
import mimetypes
import os
import stat
from collections.abc import Iterator
from pathlib import PurePosixPath

from roracle.connectors import Item


def validate_path(path: str, roots: list[str]) -> str:
    """Return realpath(path) if it is a directory equal to or under a realpath'd root, else ValueError."""
    real_roots = [os.path.realpath(r) for r in roots if r.strip()]
    if not real_roots:
        raise ValueError("filesystem connectors are disabled (no ROR_CONNECTOR_FS_ROOTS configured)")
    if not path or "\0" in path or not os.path.isabs(path):
        raise ValueError("path must be absolute")
    real = os.path.realpath(path)
    if not any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in real_roots):
        raise ValueError("path is outside the allowed connector roots")
    if not os.path.isdir(real):
        raise ValueError("path is not a directory")
    return real


def items(config: dict, roots: list[str]) -> Iterator[Item]:
    top = validate_path(config["path"], roots)  # eager: raises before iteration starts
    return _walk(top, config.get("include") or ["*"])


def _walk(top: str, include: list[str]) -> Iterator[Item]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    for dirpath, dirnames, filenames, dirfd in os.fwalk(top, follow_symlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, top)
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            rel = name if rel_dir == "." else PurePosixPath(rel_dir, name).as_posix()
            if not any(fnmatch.fnmatchcase(rel, g) for g in include):
                continue
            try:
                fd = os.open(name, flags, dir_fd=dirfd)
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOENT, errno.EACCES, errno.ENXIO, errno.EPERM):
                    continue  # symlink, vanished, unreadable, or special: skip
                raise
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    os.close(fd)
                    continue
                f = os.fdopen(fd, "rb")
            except BaseException:
                os.close(fd)
                raise
            try:
                yield Item(
                    key=rel,
                    etag=f"{st.st_mtime_ns}:{st.st_size}",
                    filename=name,
                    content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                    size=st.st_size,
                    open=lambda f=f: f,
                )
            finally:
                f.close()
