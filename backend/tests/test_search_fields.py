from datetime import UTC, datetime

from roracle import search_index
from roracle.ingest.chunking import Chunk
from roracle.models import Document, DocumentVersion
from roracle.search_fields import SEARCH_PROPS, doc_fields, head_fields, sort_key

NOW = datetime(2025, 3, 1, tzinfo=UTC)


def test_doc_fields_from_meta():
    v = DocumentVersion(filename="a.pdf", page_count=12, references=[{"title": "x"}],
                        meta={"venue": "NeurIPS", "doi": "10.1/x", "abstract": "Abs", "authors": ["Émile Zola", "B"]})  # fmt: skip
    d = Document(title="  The  Title ", created_at=NOW)
    assert doc_fields(v, d) == {"venue": "NeurIPS", "venue_sort": "neurips", "has_doi": True, "issn": [], "ids_present": ["doi"], "has_abstract": True,
                                "has_refs": True, "page_count": 12, "added_at": "2025-03-01T00:00:00+00:00",
                                "title_sort": "the title", "first_author_sort": "émile zola", "authors_text": "Émile Zola; B"}  # fmt: skip


def test_doc_fields_ids():
    v = DocumentVersion(filename="a.pdf", meta={"ids": {"issn": "0028-0836", "pmid": "1"}}, references=[])
    f = doc_fields(v, Document(title="t", created_at=NOW))
    assert (f["issn"], f["ids_present"], f["has_doi"]) == (["0028-0836"], ["issn", "pmid"], False)


def test_doc_fields_missing_meta():
    f = doc_fields(
        DocumentVersion(filename="a.pdf", meta={}, references=[]), Document(title=None, created_at=NOW)
    )
    assert (f["venue"], f["has_doi"], f["has_abstract"], f["has_refs"], f["first_author_sort"]) == (None, False, False, False, None)  # fmt: skip
    assert f["title_sort"] == "a.pdf"


def test_sort_key_caps_and_blank():
    assert sort_key("x" * 300) == "x" * 128 and sort_key("  ") is None and sort_key(None) is None


def test_head_fields():
    assert head_fields(DocumentVersion(meta={"abstract": "A"})) == {"doc_head": True, "abstract": "A"}


def test_mapping_has_marker_and_props():
    m = search_index._mapping(4)
    assert m["_meta"] == {"search_fields": 1} and set(SEARCH_PROPS) <= set(m["properties"])


def test_index_chunks_head_only_on_chunk_zero(monkeypatch):
    got = []
    monkeypatch.setattr(
        search_index.helpers, "bulk", lambda es, actions, **k: (got.extend(actions), (2, []))[1]
    )
    v = DocumentVersion(id="00000000-0000-0000-0000-000000000001", version=1, filename="a.pdf", meta={"venue": "V", "abstract": "A"}, references=[])  # fmt: skip
    d = Document(id="00000000-0000-0000-0000-000000000002", title="T", created_at=NOW)
    ch = [Chunk(0, "a", "a", [], [], []), Chunk(1, "b", "b", [], [], [])]
    search_index.index_chunks(None, v, d, ch, [[0.0], [0.0]], {}, "m", title="T", signals={"votes_net": 2})
    s0, s1 = got[0]["_source"], got[1]["_source"]
    assert s0["doc_head"] is True and s0["abstract"] == "A" and s0["votes_net"] == 2
    assert not ({"doc_head", "abstract", "votes_net"} & set(s1))
    assert s0["venue"] == s1["venue"] == "V"
