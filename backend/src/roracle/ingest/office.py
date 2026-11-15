"""Office -> PDF rendition via Gotenberg (LibreOffice)."""

import io
from uuid import UUID

import httpx
from pypdf import PdfReader

from roracle.ingest.parse import _check
from roracle.queue import NonRetryable

OFFICE_EXTS = frozenset(["doc", "docx", "odt", "rtf", "ppt", "pptx", "odp", "xls", "xlsx", "ods"])


def is_office(filename: str) -> bool:
    return filename.rsplit(".", 1)[-1].lower() in OFFICE_EXTS if "." in filename else False


def rendition_key(version_id: UUID | str) -> str:
    return f"derived/{version_id}/rendition.pdf"


def pdf_page_count(data: bytes) -> int | None:
    """Page count, or None when pypdf cannot parse it (no cap; docling decides)."""
    try:
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:  # noqa: BLE001 - any parse failure means "unknown"
        return None


def to_pdf(
    data: bytes,
    filename: str,
    url: str,
    max_bytes: int,
    timeout: float = 300.0,
    client: httpx.Client | None = None,
) -> bytes:
    """POST to Gotenberg, return PDF bytes (streamed, aborted past max_bytes).

    4xx content errors NonRetryable; others retryable.
    """
    c = client or httpx.Client(timeout=timeout)
    try:
        with c.stream(
            "POST", f"{url.rstrip('/')}/forms/libreoffice/convert", files={"files": (filename, data)}
        ) as r:
            if r.status_code >= 400:
                r.read()
                _check(r, "document could not be converted (unsupported or invalid file)")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > max_bytes:
                    raise NonRetryable("converted PDF exceeds size limit")
    finally:
        if client is None:
            c.close()
    if not buf.startswith(b"%PDF-"):
        raise ValueError("gotenberg returned a non-PDF response")
    return bytes(buf)
