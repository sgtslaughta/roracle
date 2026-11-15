"""Parse stage: docling-serve conversion and optional GROBID metadata."""

import httpx
from lxml import etree

from roracle.queue import NonRetryable

_NS = {"t": "http://www.tei-c.org/ns/1.0"}
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def convert(
    data: bytes,
    filename: str,
    docling_url: str,
    timeout: float = 300.0,
    client: httpx.Client | None = None,
) -> dict:
    """POST to docling-serve, return DoclingDocument JSON.

    5xx/transport errors raise (retryable); 4xx and empty text are NonRetryable.
    """
    c = client or httpx.Client(timeout=timeout)
    try:
        r = c.post(
            f"{docling_url.rstrip('/')}/v1/convert/file",
            files={"files": (filename, data)},
            data={"to_formats": "json", "do_ocr": "true"},
            timeout=timeout,
        )
    finally:
        if client is None:
            c.close()
    if 400 <= r.status_code < 500:
        raise NonRetryable("document could not be converted (unsupported or invalid file)")
    r.raise_for_status()
    doc = (r.json().get("document") or {}).get("json_content") or {}
    if not document_text(doc).strip():
        raise NonRetryable("no text extracted")
    return doc


def document_text(doc: dict) -> str:
    """Concatenate text items in reading order (body tree), blank-line separated."""
    texts = doc.get("texts") or []
    out: list[str] = []
    seen: set[str] = set()

    def resolve(ref: str):
        _, coll, idx = ref.split("/")
        return doc[coll][int(idx)] if coll in doc else None

    def walk(node: dict) -> None:
        for ch in node.get("children") or []:
            ref = ch.get("$ref", "")
            if ref in seen or not ref.startswith("#/"):
                continue
            seen.add(ref)
            try:
                item = resolve(ref)
            except (ValueError, IndexError):
                continue
            if item is None:
                continue
            if ref.startswith("#/texts/") and (item.get("text") or "").strip():
                out.append(item["text"].strip())
            walk(item)

    walk(doc.get("body") or {})
    if not out:  # no tree: fall back to array order
        out = [t["text"].strip() for t in texts if (t.get("text") or "").strip()]
    return "\n\n".join(out)


def _t(el, path: str) -> str | None:
    n = el.find(path, _NS)
    s = " ".join("".join(n.itertext()).split()) if n is not None else ""
    return s or None


def _authors(el) -> list[str]:
    res = []
    for a in el.findall("t:author/t:persName", _NS):
        parts = [(p.text or "").strip() for p in a.findall("t:forename", _NS)]
        parts.append((a.findtext("t:surname", default="", namespaces=_NS)).strip())
        name = " ".join(p for p in parts if p)
        if name:
            res.append(name)
    return res


def _year(el) -> int | None:
    d = el.find(".//t:imprint/t:date", _NS)
    w = (d.get("when") or "")[:4] if d is not None else ""
    return int(w) if w.isdigit() else None


def _doi(el) -> str | None:
    for i in el.findall(".//t:idno", _NS):
        if (i.get("type") or "").lower() == "doi" and (i.text or "").strip():
            return i.text.strip()
    return None


def parse_tei(xml: bytes) -> tuple[dict, list[dict]]:
    """GROBID TEI -> (meta, references) in document_versions shapes."""
    root = etree.fromstring(xml, _PARSER)
    bs = root.find(".//t:teiHeader//t:sourceDesc/t:biblStruct", _NS)
    meta = {"title": None, "authors": [], "year": None, "venue": None, "doi": None, "abstract": None}
    if bs is not None:
        ana = bs.find("t:analytic", _NS)
        meta["title"] = _t(ana, "t:title") if ana is not None else None
        meta["authors"] = _authors(ana) if ana is not None else []
        meta["year"] = _year(bs)
        meta["venue"] = _t(bs, "t:monogr/t:title")
        meta["doi"] = _doi(bs)
    if meta["title"] is None:
        meta["title"] = _t(root, ".//t:teiHeader//t:titleStmt/t:title")
    meta["abstract"] = _t(root, ".//t:teiHeader//t:abstract")
    refs = []
    for b in root.findall(".//t:back//t:listBibl/t:biblStruct", _NS):
        ana = b.find("t:analytic", _NS)
        title = (_t(ana, "t:title") if ana is not None else None) or _t(b, "t:monogr/t:title")
        refs.append(
            {
                "title": title,
                "authors": _authors(ana if ana is not None else b.find("t:monogr", _NS)),
                "year": _year(b),
                "doi": _doi(b),
            }
        )
    return meta, refs


def grobid_metadata(
    pdf: bytes, grobid_url: str, timeout: float = 300.0, client: httpx.Client | None = None
) -> tuple[dict, list[dict]]:
    c = client or httpx.Client(timeout=timeout)
    try:
        r = c.post(
            f"{grobid_url.rstrip('/')}/api/processFulltextDocument",
            files={"input": ("paper.pdf", pdf, "application/pdf")},
            timeout=timeout,
        )
    finally:
        if client is None:
            c.close()
    if 400 <= r.status_code < 500:
        raise NonRetryable("metadata extraction rejected the document")
    r.raise_for_status()
    return parse_tei(r.content)
