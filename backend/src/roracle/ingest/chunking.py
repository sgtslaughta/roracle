"""Docling HybridChunker chunking with page/bbox provenance."""

from dataclasses import dataclass
from functools import lru_cache

from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from docling_core.types.doc import DoclingDocument


@dataclass
class Chunk:
    n: int
    text: str
    contextual: str
    headings: list[str]
    pages: list[int]
    bboxes: list[dict]


@lru_cache(maxsize=4)
def _chunker(tokenizer: str, max_tokens: int) -> HybridChunker:
    tk = HuggingFaceTokenizer.from_pretrained(tokenizer, max_tokens=max_tokens)
    return HybridChunker(tokenizer=tk, merge_peers=True)


def chunk(doc: dict, title: str, tokenizer: str, max_tokens: int = 480) -> list[Chunk]:
    """Chunk a DoclingDocument dict. Token cap applies to `contextual`; doc_prompt's prefix
    adds ~10 + title tokens (fine for 2k-context embedders). `pages` may be [] for chunks
    without provenance. The cached tokenizer is shared: the worker is single-threaded per
    process (run_forever), so no lock is needed."""
    dl = DoclingDocument.model_validate(doc)
    ch = _chunker(tokenizer, max_tokens)
    out = []
    for c in ch.chunk(dl):
        if not c.text.strip():
            continue
        bboxes = [
            {
                "page": p.page_no,
                "l": p.bbox.l,
                "t": p.bbox.t,
                "r": p.bbox.r,
                "b": p.bbox.b,
                "coord_origin": p.bbox.coord_origin.value,
            }
            for item in c.meta.doc_items
            for p in item.prov
        ]
        out.append(
            Chunk(
                n=len(out),
                text=c.text,
                contextual=ch.contextualize(c),
                headings=list(c.meta.headings or []),
                pages=sorted({b["page"] for b in bboxes}),
                bboxes=bboxes,
            )
        )
    return out
