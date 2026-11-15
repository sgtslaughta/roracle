import json
from pathlib import Path

from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem
from docling_core.types.doc.base import CoordOrigin, Size

from roracle.ingest.chunking import _chunker, chunk
from roracle.ingest.embed import doc_prompt

TOK = "sentence-transformers/all-MiniLM-L6-v2"
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "docling.json").read_text())


def _big_doc() -> dict:
    doc = DoclingDocument(name="big")
    for p in range(1, 4):
        doc.add_page(page_no=p, size=Size(width=612, height=792))
    for p in range(1, 4):
        doc.add_heading(f"Section {p}", level=1)
        for i in range(25):
            doc.add_text(
                label=DocItemLabel.TEXT,
                text=f"Paragraph {i} of section {p} discusses retrieval of research notes. " * 4,
                prov=ProvenanceItem(
                    page_no=p,
                    bbox=BoundingBox(l=72, t=700 - i, r=500, b=650 - i, coord_origin=CoordOrigin.BOTTOMLEFT),
                    charspan=(0, 10),
                ),
            )
    return doc.export_to_dict()


def test_fixture_chunks():
    chunks = chunk(FIXTURE, "My Title", TOK)
    assert len(chunks) >= 1
    for i, c in enumerate(chunks):
        assert c.n == i
        assert c.pages
        assert c.bboxes and {"page", "l", "t", "r", "b", "coord_origin"} <= c.bboxes[0].keys()
        assert c.bboxes[0]["coord_origin"] == "BOTTOMLEFT"
        assert doc_prompt("My Title", c.contextual).startswith("title: My Title | text: ")
    assert sorted({p for c in chunks for p in c.pages}) == [1, 2]


def test_large_doc_token_cap_and_multi_chunks():
    chunks = chunk(_big_doc(), "T", TOK)
    assert len(chunks) > 3
    tk = _chunker(TOK, 480).tokenizer
    assert all(tk.count_tokens(c.contextual) <= 480 for c in chunks)
    assert {p for c in chunks for p in c.pages} == {1, 2, 3}
    assert any(c.headings for c in chunks)


def test_chunker_cached():
    assert _chunker(TOK, 480) is _chunker(TOK, 480)
