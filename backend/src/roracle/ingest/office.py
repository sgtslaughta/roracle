"""Office -> PDF rendition via Gotenberg (LibreOffice)."""

from uuid import UUID

import httpx

from roracle.ingest.parse import _check
from roracle.queue import NonRetryable

OFFICE_EXTS = frozenset(["doc", "docx", "odt", "rtf", "ppt", "pptx", "odp", "xls", "xlsx", "ods"])


def is_office(filename: str) -> bool:
    return filename.rsplit(".", 1)[-1].lower() in OFFICE_EXTS if "." in filename else False


def rendition_key(version_id: UUID | str) -> str:
    return f"derived/{version_id}/rendition.pdf"


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
                _check(r, "document could not be converted (unsupported or invalid file)")
            chunks, total = [], 0
            for chunk in r.iter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise NonRetryable("converted PDF exceeds size limit")
    finally:
        if client is None:
            c.close()
    pdf = b"".join(chunks)
    if not pdf.startswith(b"%PDF-"):
        raise ValueError("gotenberg returned a non-PDF response")
    return pdf
