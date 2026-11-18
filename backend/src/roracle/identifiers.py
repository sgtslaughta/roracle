"""Persistent-identifier extraction and canonicalisation (pure functions, no I/O).

Schemes: doi, arxiv, pmid, pmcid, isbn, acl, openreview. `extract_ids` is label/URL-anchored: a bare number or
arbitrary word is never an identifier."""

import re
from urllib.parse import unquote

SCHEMES = ("doi", "arxiv", "pmid", "pmcid", "isbn", "acl", "openreview", "issn", "eissn")
# issn/eissn identify a JOURNAL and isbn a proceedings/book container: shown to users, never matched.
MATCHABLE = tuple(x for x in SCHEMES if x not in ("issn", "eissn", "isbn"))

_ARX_NEW = r"\d{4}\.\d{4,5}"
_ARX_OLD = r"[a-z\-]+(?:\.[a-z]{2})?/\d{7}"
_ARX_ID = rf"(?:{_ARX_NEW}|{_ARX_OLD})"
_ACL_ID = r"(?:\d{4}\.[a-z0-9\-]+\.\d+|[a-z]\d{2}-\d{4})"
_ACL_PATH = r"(?:pdf/|[a-z]/[a-z]\d{2}/)?"  # new /pdf/ form or old /P/P19/ directories
_DOI = r"10\.\d{4,9}/[^\s\"'<>]{1,300}"  # cap: bounds strip loop (untrusted PDF text)
_I = re.IGNORECASE

_EXTRACT = {
    "doi": re.compile(rf"(?<!\w)({_DOI})", _I),
    "arxiv": re.compile(
        rf"(?:arxiv(?:\s+preprint)?\s*:?\s*(?:arxiv\s*:\s*)?(?:abs/)?|arxiv\.org/(?:abs|pdf)/|corr\W{{1,3}}abs/)"
        rf"({_ARX_ID})(?:v\d+)?(?![\d])",
        _I,
    ),
    "pmid": re.compile(r"(?:\bpmid\s*:?\s*|pubmed\.ncbi\.nlm\.nih\.gov/)(\d{1,9})\b", _I),
    "pmcid": re.compile(r"\b(PMC\d{4,9})\b", _I),
    "isbn": re.compile(r"\bisbn(?:-1[03]|\s1[03](?=\s*:|\s+97[89]))?\s*:?\s*((?:\d[\- ]?){9,12}[\dX])", _I),
    "acl": re.compile(rf"(?:aclanthology\.org|aclweb\.org/anthology)/{_ACL_PATH}({_ACL_ID})(?![\w\-])", _I),
    "openreview": re.compile(r"openreview\.net/(?:forum|pdf)\?id=([\w\-]+)", _I),
}
_TRAIL = ".,;:)]}"
_OPEN = {")": "(", "]": "[", "}": "{"}
# the arXiv margin stamp: the id must be followed by the [category] bracket (a footnote citing BERT has none)
_ARX_STAMP = re.compile(rf"arxiv:({_ARX_ID})(?:v\d+)?\s*\[([a-z\-]+(?:\.[a-z\-]+)?)\]", _I)
_ISSN = re.compile(r"\b(e-?)?issn\s*:?\s*(\d{4}-?\d{3}[\dX])\b", _I)


def _isbn_ok(d: str) -> bool:
    if len(d) == 10:
        if not re.fullmatch(r"\d{9}[\dX]", d):
            return False
        return sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(d)) % 11 == 0
    if len(d) == 13 and d.isdigit():
        return sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(d)) % 10 == 0
    return False


def _isbn13(d: str) -> str | None:
    if not _isbn_ok(d):
        return None
    if len(d) == 13:
        return d
    core = "978" + d[:9]
    return core + str((10 - sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(core)) % 10) % 10)


def _norm_issn(v: str) -> str | None:
    m = re.fullmatch(r"(?i)\s*(?:e?-?issn\s*:?\s*)?(\d{4})-?(\d{3}[\dX])\s*", v)
    if not m:
        return None
    d = (m[1] + m[2]).upper()
    chk = (11 - sum((8 - i) * int(c) for i, c in enumerate(d[:7])) % 11) % 11
    return f"{d[:4]}-{d[4:]}" if d[7] == ("X" if chk == 10 else str(chk)) else None


def _norm_isbn(v: str) -> str | None:
    d = re.sub(r"[^0-9X]", "", re.sub(r"(?i)^\s*isbn(?:-1[03]|\s+1[03](?=\s*:))?\s*:?", "", v).upper())
    # a captured run may swallow trailing digits (a year): try the plausible lengths in order
    if d[:3] in ("978", "979") and len(d) >= 13:  # a bad ISBN-13 must not fall back to its first 10 digits
        return _isbn13(d[:13])
    for n in (10, 13):
        if len(d) >= n and (r := _isbn13(d[:n])):
            return r
    return None


def _norm_arxiv(v: str) -> str | None:
    s = re.sub(r"(?i)^\s*(arxiv\s*:\s*|https?://(www\.)?arxiv\.org/(abs|pdf)/)", "", unquote(v)).strip()
    s = re.sub(r"(?i)(v\d+)?/?$", "", re.sub(r"(?i)\.pdf/?$", "", s))
    if re.fullmatch(_ARX_NEW, s):
        return s if 1 <= int(s[2:4]) <= 12 else None  # YYMM: reject month 00/13+
    m = re.fullmatch(r"([a-z\-]+)(?:\.[a-z]{2})?/(\d{7})", s, _I)  # the subject class is not part of the id
    return f"{m[1].lower()}/{m[2]}" if m else None


def _norm_acl(v: str) -> str | None:
    s = re.sub(
        r"(?i)^\s*https?://(www\.)?(aclanthology\.org|aclweb\.org/anthology)/(pdf/|[a-z]/[a-z]\d{2}/)?", "", v
    ).strip()
    s = re.sub(r"(?i)(\.pdf)?/?$", "", s)
    if re.fullmatch(_ACL_ID, s, _I):
        return s.upper() if re.fullmatch(r"[a-z]\d{2}-\d{4}", s, _I) else s.lower()
    return None


def _norm_doi(v: str) -> str | None:
    s = unquote(str(v or "")).strip()
    s = re.sub(r"(?i)^(https?://(www\.)?(dx\.)?doi\.org/|doi:\s*)", "", s).strip().lower()
    if len(s) > 320:  # ponytail: length cap keeps the strip below O(n^2) blow-up on hostile input
        return None
    while s and s[-1] in _TRAIL:
        c = s[-1]
        if c in _OPEN and s.count(c) <= s.count(_OPEN[c]):  # balanced bracket: part of the DOI
            break
        s = s[:-1]
    return s if re.match(r"10\.\d+/\S", s) and not re.search(r"\s", s) else None


def normalize(scheme: str, value) -> str | None:
    """Canonical form of `value` under `scheme`, or None when it is not a valid identifier of that scheme."""
    v = str(value or "").strip()
    if not v:
        return None
    if scheme == "doi":
        return _norm_doi(v)
    if scheme == "arxiv":
        return _norm_arxiv(v)
    if scheme == "pmid":
        m = re.fullmatch(r"(?i)(?:pmid\s*:?\s*)?(\d{1,9})", v)
        return str(int(m[1])) if m and int(m[1]) else None
    if scheme == "pmcid":
        m = re.fullmatch(r"(?i)(?:pmc)?(\d{4,9})", v)
        return f"PMC{m[1]}" if m else None
    if scheme in ("issn", "eissn"):
        return _norm_issn(v)
    if scheme == "isbn":
        return _norm_isbn(v)
    if scheme == "acl":
        return _norm_acl(v)
    if scheme == "openreview":
        m = re.fullmatch(r"(?:https?://(?:www\.)?openreview\.net/(?:forum|pdf)\?id=)?([\w\-]+)", v, _I)
        return m[1] if m else None
    return None


def add_id(ids: dict[str, str], scheme: str, value) -> None:
    """Normalise and add (first value per scheme wins); DOIs of arXiv/ACL also yield those schemes."""
    n = normalize(scheme, value)
    if n is None:
        return
    ids.setdefault(scheme, n)
    if scheme == "doi":
        if n.startswith("10.48550/arxiv."):
            add_id(ids, "arxiv", n[len("10.48550/arxiv.") :])
        elif n.startswith("10.18653/v1/"):
            add_id(ids, "acl", n[len("10.18653/v1/") :])


def extract_ids(text: str) -> dict[str, str]:
    """Identifiers found in free text, scheme -> canonical value."""
    ids: dict[str, str] = {}
    text = text or ""
    for scheme, rx in _EXTRACT.items():
        for m in rx.finditer(text):
            before = len(ids)
            add_id(ids, scheme, m[1] if scheme == "doi" else m[1].rstrip(_TRAIL))
            if scheme != "doi" and len(ids) > before:
                break
            if scheme == "doi" and "doi" in ids:
                break
    for m in _ISSN.finditer(text):
        add_id(ids, "eissn" if m[1] else "issn", m[2])
    return ids


def extract_arxiv_stamp(text: str) -> dict[str, str]:
    """The paper's OWN arXiv id + primary category from the first-page margin stamp `arXiv:<id>[vN] [cs.XX]`
    (`{"arxiv": id, "category": "cs.XX"}`); none when the text holds no stamp or several different ones."""
    found = {(n, m[2]) for m in _ARX_STAMP.finditer(text or "") if (n := normalize("arxiv", m[1]))}
    if len({n for n, _ in found}) != 1:
        return {}
    (n, cat), *_ = found
    return {"arxiv": n, "category": cat}


def public_ids(meta: dict) -> dict[str, str]:
    """A version's identifiers for clients: `meta.ids` strings plus a legacy top-level `meta.doi`."""
    raw = meta.get("ids") if isinstance(meta.get("ids"), dict) else {}
    out = {k: v for k, v in raw.items() if isinstance(v, str) and v}
    if isinstance(meta.get("doi"), str) and meta["doi"] and "doi" not in out:
        out["doi"] = meta["doi"]
    return out
