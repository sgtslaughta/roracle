"""Parse stage: docling-serve conversion and optional GROBID metadata."""

import re

import httpx
from lxml import etree

from roracle.queue import NonRetryable

_NS = {"t": "http://www.tei-c.org/ns/1.0"}
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


_NONRETRY = {400, 413, 415, 422}
_COLLS = {"texts", "groups", "tables", "pictures", "key_value_items", "form_items"}
_MAX_DEPTH = 100


def _check(r: httpx.Response, msg: str) -> None:
    if r.status_code in _NONRETRY:
        raise NonRetryable(msg)
    r.raise_for_status()


def convert(
    data: bytes,
    filename: str,
    docling_url: str,
    timeout: float = 300.0,
    client: httpx.Client | None = None,
) -> dict:
    """POST to docling-serve, return DoclingDocument JSON.

    400/413/415/422 and empty text are NonRetryable; other errors raise (retryable).
    """
    c = client or httpx.Client(timeout=timeout)
    try:
        r = c.post(
            f"{docling_url.rstrip('/')}/v1/convert/file",
            files={"files": (filename, data)},
            data={"to_formats": "json", "do_ocr": "true", "image_export_mode": "placeholder"},
        )
    finally:
        if client is None:
            c.close()
    _check(r, "document could not be converted (unsupported or invalid file)")
    try:
        body = r.json()
        doc = body["document"]["json_content"]
        doc.get("texts")
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise ValueError("docling-serve returned an unexpected response") from e
    if not document_text(doc).strip():
        raise NonRetryable("no text extracted")
    return doc


def _table_text(item: dict) -> str:
    rows: dict[int, dict[int, str]] = {}
    for cell in (item.get("data") or {}).get("table_cells") or []:
        t = (cell.get("text") or "").strip()
        if t:
            r = cell.get("start_row_offset_idx") or 0
            rows.setdefault(r, {})[cell.get("start_col_offset_idx") or 0] = t
    return "\n".join(" | ".join(cols[k] for k in sorted(cols)) for _, cols in sorted(rows.items()))


def document_text(doc: dict) -> str:
    """Concatenate text and table items in reading order (body tree), blank-line separated."""
    out: list[str] = []
    seen: set[str] = set()

    def resolve(ref: str):
        parts = ref.split("/")
        if len(parts) != 3 or parts[0] != "#" or parts[1] not in _COLLS or not parts[2].isdecimal():
            return None
        coll = doc.get(parts[1])
        idx = int(parts[2])
        return coll[idx] if isinstance(coll, list) and 0 <= idx < len(coll) else None

    def emit(ref: str, item: dict) -> None:
        txt = _table_text(item) if ref.startswith("#/tables/") else (item.get("text") or "").strip()
        if ref.startswith(("#/texts/", "#/tables/")) and txt:
            out.append(txt)

    stack = [(c, 0) for c in reversed((doc.get("body") or {}).get("children") or [])]
    while stack:
        ch, depth = stack.pop()
        ref = ch.get("$ref", "") if isinstance(ch, dict) else ""
        if ref in seen or depth > _MAX_DEPTH:
            continue
        item = resolve(ref)
        if item is None:
            continue
        seen.add(ref)
        emit(ref, item)
        stack.extend((c, depth + 1) for c in reversed(item.get("children") or []))
    if not out:  # no tree: fall back to array order
        for i, t in enumerate(doc.get("texts") or []):
            emit(f"#/texts/{i}", t)
    return "\n\n".join(out)


def _t(el, path: str) -> str | None:
    n = el.find(path, _NS)
    s = " ".join("".join(n.itertext()).split()) if n is not None else ""
    return s or None


def _authors(el) -> list[str]:
    if el is None:
        return []
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
    if d is None:
        return None
    m = re.match(r"\s*([0-9]{4})", d.get("when") or "".join(d.itertext()))
    return int(m.group(1)) if m else None


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
        meta["authors"] = _authors(ana) or _authors(bs.find("t:monogr", _NS))
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
    pdf: bytes, grobid_url: str, timeout: float = 120.0, client: httpx.Client | None = None
) -> tuple[dict, list[dict]]:
    """Optional enrichment: content problems give ({}, []); transport/5xx errors raise."""
    c = client or httpx.Client(timeout=timeout)
    try:
        r = c.post(
            f"{grobid_url.rstrip('/')}/api/processFulltextDocument",
            files={"input": ("paper.pdf", pdf, "application/pdf")},
        )
    finally:
        if client is None:
            c.close()
    _check(r, "metadata extraction rejected the document")
    if r.status_code == 204 or not r.content.strip():
        return {}, []
    try:
        return parse_tei(r.content)
    except etree.XMLSyntaxError:
        return {}, []
