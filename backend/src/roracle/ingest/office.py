"""Office -> PDF rendition via Gotenberg (LibreOffice)."""

from uuid import UUID

import httpx

from roracle.ingest.parse import _check

OFFICE_EXTS = frozenset(["doc", "docx", "odt", "rtf", "ppt", "pptx", "odp", "xls", "xlsx", "ods"])


def is_office(filename: str) -> bool:
    return filename.rsplit(".", 1)[-1].lower() in OFFICE_EXTS if "." in filename else False


def rendition_key(version_id: UUID | str) -> str:
    return f"derived/{version_id}/rendition.pdf"


def to_pdf(
    data: bytes,
    filename: str,
    url: str,
    timeout: float = 300.0,
    client: httpx.Client | None = None,
) -> bytes:
    """POST to Gotenberg, return PDF bytes. 4xx content errors NonRetryable; others retryable."""
    c = client or httpx.Client(timeout=timeout)
    try:
        r = c.post(f"{url.rstrip('/')}/forms/libreoffice/convert", files={"files": (filename, data)})
    finally:
        if client is None:
            c.close()
    _check(r, "document could not be converted (unsupported or invalid file)")
    if not r.content.startswith(b"%PDF-"):
        raise ValueError("gotenberg returned a non-PDF response")
    return r.content
