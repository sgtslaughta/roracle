"""Filesystem source. Cannot leave its allowlisted roots.

Guarantees: the configured path's realpath must be a root or below one (checked at create and every
run); the root is opened and the path below it descended one O_NOFOLLOW|O_DIRECTORY component at a time
(a mid-path symlink swap after validation fails the run); the walk never follows directory symlinks
(os.fwalk follow_symlinks=False); each file is lstat'ed, then opened
relative to its directory fd with O_NOFOLLOW and must be a regular file (fstat on the opened fd, so
no check/use gap). FIFOs/devices are never read (O_NONBLOCK keeps the open from blocking).
Residual: hardlinks to files outside the root are indistinguishable from regular files (not skipped);
a writer who can swap a parent of the *root itself* could redirect it. Mount roots read-only.
"""

import errno
import fnmatch
import mimetypes
import os
import stat
import time
from collections.abc import Iterator
from pathlib import PurePosixPath

from roracle.connectors import Item, RunDeadline


def validate_path(path: str, roots: list[str]) -> tuple[str, str]:
    """Return (realpath(path), matching real root) if path is a directory equal to or under a realpath'd root."""
    real_roots = [os.path.realpath(r) for r in roots if r.strip()]
    if not real_roots:
        raise ValueError("filesystem connectors are disabled (no ROR_CONNECTOR_FS_ROOTS configured)")
    if not path or "\0" in path or not os.path.isabs(path):
        raise ValueError("path must be absolute")
    real = os.path.realpath(path)
    root = next((r for r in real_roots if real == r or real.startswith(r.rstrip(os.sep) + os.sep)), None)
    if root is None:
        raise ValueError("path is outside the allowed connector roots")
    if not os.path.isdir(real):
        raise ValueError("path is not a directory")
    return real, root


def items(config: dict, roots: list[str], deadline: float | None = None) -> Iterator[Item]:
    top, root = validate_path(config["path"], roots)  # eager: raises before iteration starts
    return _walk(top, root, config.get("include") or ["*"], deadline)


def _open_top(top: str, root: str) -> int:
    """Open `top` component by component below `root` with O_NOFOLLOW: a symlink swapped in after
    validation fails the open instead of redirecting the walk."""
    dflags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    fd = os.open(root, dflags)
    try:
        rel = os.path.relpath(top, root)
        for part in [] if rel == "." else rel.split(os.sep):
            nxt = os.open(part, dflags | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _walk(top: str, root: str, include: list[str], deadline: float | None) -> Iterator[Item]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    topfd = _open_top(top, root)
    try:
        yield from _files(topfd, include, flags, deadline)
    finally:
        os.close(topfd)


def _files(topfd: int, include: list[str], flags: int, deadline: float | None) -> Iterator[Item]:
    for dirpath, dirnames, filenames, dirfd in os.fwalk(".", dir_fd=topfd, follow_symlinks=False):
        if deadline is not None and time.monotonic() > deadline:
            raise RunDeadline("run time cap reached")
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, ".")
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            rel = name if rel_dir == "." else PurePosixPath(rel_dir, name).as_posix()
            if not any(fnmatch.fnmatchcase(rel, g) for g in include):
                continue
            try:
                if not stat.S_ISREG(os.stat(name, dir_fd=dirfd, follow_symlinks=False).st_mode):
                    continue  # symlink, FIFO, device (lstat, before any open)
                fd = os.open(name, flags, dir_fd=dirfd)
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOENT, errno.EACCES, errno.ENXIO, errno.EPERM):
                    continue  # vanished, unreadable, or swapped for a symlink
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
