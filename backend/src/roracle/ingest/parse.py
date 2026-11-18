"""Parse stage: docling-serve conversion and optional GROBID metadata."""

import re

import httpx
from lxml import etree

from roracle.identifiers import SCHEMES, add_id, extract_arxiv_stamp, extract_ids
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


_TRANSIENT = ("timeout", "timed out", "connection", "unavailable", "temporar", "memory")


def _permanent_failure(body) -> bool:
    """docling answers 200 with status=failure for deterministic input problems (fake PDF, binary)."""
    if not isinstance(body, dict) or body.get("status") != "failure":
        return False
    errs = [e for e in body.get("errors") or [] if isinstance(e, dict)]
    text = " ".join(str(e.get("error_message", "")) for e in errs).lower()
    return not any(t in text for t in _TRANSIENT)


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
        if _permanent_failure(body):
            raise NonRetryable("document could not be converted (unsupported or invalid file)")
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


def merge_page_ids(meta: dict, doc: dict) -> dict:
    """Add the arXiv margin stamp of the FIRST page to meta["ids"]; GROBID's values win. Only a parsed GROBID
    answer (it carries `ids`) is enriched, so a version GROBID never parsed stays eligible for reparse."""
    if "ids" not in meta:
        return meta
    page1 = " ".join(
        str(t.get("text") or "")
        for t in doc.get("texts") or []
        if any(isinstance(p, dict) and p.get("page_no") == 1 for p in t.get("prov") or [])
    )
    stamp = extract_arxiv_stamp(page1)
    cat = stamp.pop("category", None)
    return {**meta, "ids": {**stamp, **meta["ids"]}, **({"arxiv_category": cat} if cat else {})}


MAX_KEYWORDS = 20


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


def _ids(el, *, text: str = "", own: bool = False) -> dict[str, str]:
    """Identifiers of one biblStruct: typed <idno>s first, then <ptr target> URLs, then label-anchored text.
    `own` (the paper's header): a scheme with several distinct values is ambiguous and dropped."""
    found: list[dict[str, str]] = []
    for i in el.findall(".//t:idno", _NS):
        typ = (i.get("type") or "").lower()
        typ = "eissn" if typ == "issne" else typ
        if typ in SCHEMES and (i.text or "").strip():
            one: dict[str, str] = {}
            add_id(one, typ, i.text)
            found.append(one)
    found.append(
        extract_ids(" ".join(p.get("target") or "" for p in el.findall(".//t:ptr", _NS)) + " " + text)
    )
    ids: dict[str, str] = {}
    for one in found:
        for k, v in one.items():
            ids.setdefault(k, v)
    if own:
        ids = {k: v for k, v in ids.items() if {o[k] for o in found if k in o} == {v}}
    return ids


def _raw(el) -> str:
    return (_t(el, "t:note[@type='raw_reference']") or "")[:1000]


def parse_tei(xml: bytes) -> tuple[dict, list[dict]]:
    """GROBID TEI -> (meta, references) in document_versions shapes."""
    root = etree.fromstring(xml, _PARSER)
    bs = root.find(".//t:teiHeader//t:sourceDesc/t:biblStruct", _NS)
    meta = {
        "title": None,
        "authors": [],
        "year": None,
        "venue": None,
        "doi": None,
        "ids": {},
        "abstract": None,
    }
    if bs is not None:
        ana = bs.find("t:analytic", _NS)
        meta["title"] = _t(ana, "t:title") if ana is not None else None
        meta["authors"] = _authors(ana) or _authors(bs.find("t:monogr", _NS))
        meta["year"] = _year(bs)
        meta["venue"] = _t(bs, "t:monogr/t:title")
        meta["ids"] = _ids(bs, own=True)  # biblStruct only: abstract/body cite OTHER papers
        meta["doi"] = meta["ids"].get("doi")
    if meta["title"] is None:
        meta["title"] = _t(root, ".//t:teiHeader//t:titleStmt/t:title")
    meta["abstract"] = _t(root, ".//t:teiHeader//t:abstract")
    terms = [
        " ".join("".join(k.itertext()).split())
        for k in root.findall(".//t:teiHeader//t:keywords/t:term", _NS)
    ]
    if kw := [k for k in terms if k][:MAX_KEYWORDS]:  # author-declared: only a hint for auto-tags
        meta["keywords"] = kw
    refs = []
    for b in root.findall(".//t:back//t:listBibl/t:biblStruct", _NS):
        ana = b.find("t:analytic", _NS)
        title = (_t(ana, "t:title") if ana is not None else None) or _t(b, "t:monogr/t:title")
        raw, ids = _raw(b), _ids(b, text=_raw(b))
        refs.append(
            {
                "title": title,
                "authors": _authors(ana if ana is not None else b.find("t:monogr", _NS)),
                "year": _year(b),
                "doi": ids.get("doi"),
                "ids": ids,
                "raw": raw,
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
            data={"includeRawCitations": "1"},
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
