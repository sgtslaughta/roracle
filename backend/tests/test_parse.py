import json
from pathlib import Path

import httpx
import pytest

from roracle.ingest.parse import convert, document_text, parse_tei
from roracle.queue import NonRetryable

FX = Path(__file__).parent / "fixtures"


def _client(status=200, doc=None):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/convert/file"
        return httpx.Response(status, json={"document": {"json_content": doc}} if doc is not None else {})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_document_text_has_known_sentence():
    doc = json.loads((FX / "docling.json").read_text())
    assert "quokka heuristic reranks passages" in document_text(doc)


def test_document_text_follows_body_order():
    doc = {
        "body": {"children": [{"$ref": "#/texts/1"}, {"$ref": "#/groups/0"}]},
        "groups": [{"children": [{"$ref": "#/texts/0"}]}],
        "texts": [{"text": "second"}, {"text": "first"}],
    }
    assert document_text(doc) == "first\n\nsecond"


def test_convert_returns_doc():
    doc = json.loads((FX / "docling.json").read_text())
    out = convert(b"x", "a.pdf", "http://d", client=_client(doc=doc))
    assert out["texts"]


def test_convert_no_text_nonretryable():
    with pytest.raises(NonRetryable, match="no text extracted"):
        convert(b"x", "a.pdf", "http://d", client=_client(doc={"texts": [{"text": "  "}]}))


def test_convert_4xx_nonretryable():
    with pytest.raises(NonRetryable):
        convert(b"x", "a.xyz", "http://d", client=_client(status=415))


def test_convert_5xx_retryable():
    with pytest.raises(httpx.HTTPStatusError):
        convert(b"x", "a.pdf", "http://d", client=_client(status=503))


def test_parse_tei():
    meta, refs = parse_tei((FX / "grobid_tei.xml").read_bytes())
    assert meta["title"] == "Sparse Retrieval for Lab Notebooks"
    assert meta["authors"] == ["Ada Lovelace", "Alan M Turing"]
    assert meta["doi"] == "10.1234/jar.2022.001"
    assert meta["year"] == 2022
    assert meta["venue"] == "Journal of Applied Retrieval"
    assert meta["abstract"].startswith("We study retrieval")
    assert len(refs) == 3
    assert refs[0] == {
        "title": "Dense retrieval basics",
        "authors": ["J Smith"],
        "year": 2020,
        "doi": "10.1000/dense.2020",
    }
    assert refs[1]["doi"] is None and len(refs[1]["authors"]) == 2


def _reachable():
    try:
        return httpx.get("http://localhost:5001/health", timeout=2).is_success
    except httpx.HTTPError:
        return False


@pytest.mark.integration
@pytest.mark.parametrize("name,needle", [("sample.md", "quokka"), ("sample.pdf", "quokka")])
def test_convert_real(name, needle):
    if not _reachable():
        pytest.skip("docling-serve unreachable")
    doc = convert((FX / name).read_bytes(), name, "http://localhost:5001")
    assert needle in document_text(doc)
