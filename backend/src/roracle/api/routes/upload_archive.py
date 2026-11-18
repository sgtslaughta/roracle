"""Archive uploads: a ZIP/tar/tar.gz that is not LaTeX source is unpacked here, one document per file."""

import mimetypes
from collections.abc import Callable
from pathlib import Path

from fastapi import HTTPException

from roracle.ingest.archive import Archive, ArchiveError, kind_of, spool
from roracle.ingest.formats import latex_main
from roracle.storage import BlobStore, EmptyFile, StagedBlob, StagedFile, TooLarge, stage

Accept = Callable[..., dict]


def _local(store: BlobStore, staged: StagedFile | StagedBlob, cap: int) -> tuple[Path, bool]:
    """(path to read, whether we own it)."""
    if isinstance(staged, StagedFile):
        return Path(staged.path), False
    stream, _ = store.open_key(staged.upload.key)
    try:
        return spool(stream, cap), True
    finally:
        stream.close()


def unpack(
    accept_one: Accept, store: BlobStore, staged: StagedFile | StagedBlob, form, filename: str, cap: int
) -> dict | None:
    """None when `filename` is not an archive or is LaTeX source (the caller stores it as one paper).
    Otherwise the per-file results; a file that cannot be added is listed in `skipped`, not an error."""
    if kind_of(filename) is None:
        return None
    path, owned = _local(store, staged, cap)
    try:
        try:
            a = Archive(path, filename, cap)
            is_tex = latex_main(a) is not None
        except (ArchiveError, TooLarge) as e:
            raise HTTPException(422, f"archive could not be read: {e}") from None
        try:
            if is_tex:
                return None
            return _add_all(accept_one, a, form.model_copy(update={"title": None}), cap)
        finally:
            a.close()
    finally:
        if owned:
            path.unlink(missing_ok=True)


def _add_all(accept_one: Accept, a: Archive, form, cap: int) -> dict:
    docs: list[dict] = []
    skipped = [{"name": n, "reason": r} for n, r in a.skipped]
    for i, m in enumerate(a.members):
        try:
            s = stage(a.open(m), cap)
        except TooLarge:
            skipped.append({"name": m.name, "reason": f"file exceeds the {cap // (1024 * 1024)} MB limit"})
            continue
        except EmptyFile:
            skipped.append({"name": m.name, "reason": "empty file"})
            continue
        except ArchiveError as e:  # total unpacked size: nothing more can be read
            skipped += [{"name": r.name, "reason": str(e)} for r in a.members[i:]]
            break
        try:
            res = accept_one(
                staged=s, form=form, filename=m.basename,
                content_type=mimetypes.guess_type(m.basename)[0] or "application/octet-stream",
            )  # fmt: skip
            docs.append({**res, "filename": m.basename})
        except HTTPException as e:
            if e.status_code == 409:
                skipped.append({"name": m.name, "reason": "already in the library"})
            elif e.status_code == 413:
                skipped.append({"name": m.name, "reason": str(e.detail)})
            else:
                raise
        finally:
            Path(s.path).unlink(missing_ok=True)
    return {"archive": True, "documents": docs, "skipped": skipped}
