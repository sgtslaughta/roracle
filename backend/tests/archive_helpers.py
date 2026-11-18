"""Build small zip/tar archives in memory for archive tests."""

import gzip
import io
import stat
import tarfile
import zipfile


def make_zip(files: dict[str, bytes], extra=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        for zi, data in extra or []:
            z.writestr(zi, data)
    return buf.getvalue()


def zip_symlink(name: str, target: bytes = b"/etc/passwd"):
    zi = zipfile.ZipInfo(name)
    zi.external_attr = (stat.S_IFLNK | 0o777) << 16
    return zi, target


def make_tar(files: dict[str, bytes], gz: bool = False, special=()) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz" if gz else "w") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mtime = 1700000000
            t.addfile(ti, io.BytesIO(data))
        for name, typ in special:
            ti = tarfile.TarInfo(name)
            ti.type = typ
            ti.linkname = "/etc/passwd"
            t.addfile(ti)
    return buf.getvalue()


def make_gz(data: bytes) -> bytes:
    return gzip.compress(data)
