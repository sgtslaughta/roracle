import json
from pathlib import Path

import httpx
import pytest

from roracle.ingest.parse import convert, document_text, grobid_metadata, parse_tei
from roracle.queue import NonRetryable

FX = Path(__file__).parent / "fixtures"


def _client(status=200, doc=None):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/convert/file"
        body = req.content
        assert b'name="files"' in body
        for k, v in (("to_formats", "json"), ("do_ocr", "true"), ("image_export_mode", "placeholder")):
            assert f'name="{k}"'.encode() in body and v.encode() in body
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


@pytest.mark.parametrize("code", [401, 403, 404, 408, 429])
def test_convert_other_4xx_retryable(code):
    with pytest.raises(httpx.HTTPStatusError):
        convert(b"x", "a.pdf", "http://d", client=_client(status=code))


def test_convert_garbage_200_clear_error():
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="nope")))
    with pytest.raises(ValueError, match="unexpected response"):
        convert(b"x", "a.pdf", "http://d", client=c)


def test_table_only_doc_has_text():
    doc = {
        "body": {"children": [{"$ref": "#/tables/0"}]},
        "tables": [
            {
                "children": [],
                "data": {
                    "table_cells": [
                        {"text": "B", "start_row_offset_idx": 0, "start_col_offset_idx": 1},
                        {"text": "A", "start_row_offset_idx": 0, "start_col_offset_idx": 0},
                        {"text": "1", "start_row_offset_idx": 1, "start_col_offset_idx": 0},
                    ]
                },
            }
        ],
    }
    assert document_text(doc) == "A | B\n1"
    assert convert(b"x", "a.pdf", "http://d", client=_client(doc=doc)) == doc


def test_document_text_bad_refs_and_deep_nesting():
    doc = {
        "body": {"children": [{"$ref": "#/texts/9"}, {"$ref": "#/evil/0"}, {"$ref": "#/groups/0"}]},
        "groups": [{"children": [{"$ref": "#/groups/1"}]}, {"children": [{"$ref": "#/groups/0"}]}],
        "texts": [],
    }
    assert document_text(doc) == ""


def test_parse_tei_empty_bibl_and_monogr_authors():
    xml = (
        b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><sourceDesc><biblStruct>'
        b"<monogr><author><persName><forename>A</forename><surname>B</surname></persName></author>"
        b"<imprint><date>2018 spring</date></imprint></monogr></biblStruct></sourceDesc></fileDesc></teiHeader>"
        b"<text><back><listBibl><biblStruct/></listBibl></back></text></TEI>"
    )
    meta, refs = parse_tei(xml)
    assert meta["authors"] == ["A B"] and meta["year"] == 2018
    assert refs == [{"title": None, "authors": [], "year": None, "doi": None}]


def test_parse_tei_xxe_not_resolved():
    xml = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
        b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt><title>&e;</title>'
        b"</titleStmt></fileDesc></teiHeader></TEI>"
    )
    meta, _ = parse_tei(xml)
    assert "root:" not in (meta["title"] or "")


def _grobid(status, content=b""):
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda r: (
                None
                if r.url.path != "/api/processFulltextDocument" or b'name="input"' not in r.content
                else httpx.Response(status, content=content)
            )
        )
    )


def test_grobid_ok_empty_and_errors():
    tei = (FX / "grobid_tei.xml").read_bytes()
    meta, refs = grobid_metadata(b"%PDF", "http://g", client=_grobid(200, tei))
    assert meta["doi"] and len(refs) == 3
    assert grobid_metadata(b"%PDF", "http://g", client=_grobid(204)) == ({}, [])
    assert grobid_metadata(b"%PDF", "http://g", client=_grobid(200, b"not xml")) == ({}, [])
    with pytest.raises(NonRetryable):
        grobid_metadata(b"%PDF", "http://g", client=_grobid(415))
    with pytest.raises(httpx.HTTPStatusError):
        grobid_metadata(b"%PDF", "http://g", client=_grobid(503))


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
