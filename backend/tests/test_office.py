import io
import os
import zipfile

import httpx
import pytest

from roracle.ingest.office import is_office, rendition_key, to_pdf
from roracle.queue import NonRetryable

GOT = os.environ.get("ROR_GOTENBERG_URL") or "http://localhost:3300"


def _client(status=200, body=b"%PDF-1.7 x"):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/forms/libreoffice/convert"
        assert b'name="files"; filename="a.docx"' in req.content
        return httpx.Response(status, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_to_pdf_returns_pdf():
    assert to_pdf(b"x", "a.docx", "http://g/", client=_client()).startswith(b"%PDF-")


@pytest.mark.parametrize("status", [400, 413, 415, 422])
def test_to_pdf_4xx_nonretryable(status):
    with pytest.raises(NonRetryable, match="could not be converted"):
        to_pdf(b"x", "a.docx", "http://g", client=_client(status))


def test_to_pdf_5xx_retryable():
    with pytest.raises(httpx.HTTPStatusError):
        to_pdf(b"x", "a.docx", "http://g", client=_client(503))


def test_to_pdf_non_pdf_body():
    with pytest.raises(ValueError):
        to_pdf(b"x", "a.docx", "http://g", client=_client(body=b"<html>"))


@pytest.mark.parametrize(
    "name,expected",
    [("a.docx", True), ("A.DOCX", True), ("x.y.xlsx", True), ("a.odp", True), ("a.pdf", False), ("a", False)],
)
def test_is_office(name, expected):
    assert is_office(name) is expected


def test_rendition_key():
    assert rendition_key("abc") == "derived/abc/rendition.pdf"


def _docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Hello gotenberg</w:t></w:r></w:p></w:body></w:document>",
        )
    return buf.getvalue()


def _up() -> bool:
    try:
        return httpx.get(f"{GOT}/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.integration
def test_real_gotenberg():
    if not _up():
        pytest.skip("gotenberg not reachable")
    assert to_pdf(_docx(), "t.docx", GOT, timeout=120).startswith(b"%PDF-")
