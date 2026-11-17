"""Identifier helpers over `version.meta["ids"]` (read defensively: older rows lack it)."""

import re

ID_KINDS = ("doi", "arxiv", "pmid", "pmcid", "isbn", "acl", "openreview", "issn")
ISSN_RE = re.compile(r"^\d{4}-\d{3}[\dX]$")


def ids_of(meta: dict | None) -> dict[str, str]:
    raw = (meta or {}).get("ids")
    out = {k: v for k, v in (raw if isinstance(raw, dict) else {}).items() if isinstance(v, str) and v}
    legacy = (meta or {}).get("doi")
    if "doi" not in out and isinstance(legacy, str) and legacy:
        out["doi"] = legacy
    return out


def canon_issn(s: str) -> str | None:
    s = s.strip().upper()
    return s if ISSN_RE.match(s) else None


def issns(meta: dict | None) -> list[str]:
    ids = ids_of(meta)
    return sorted({c for k in ("issn", "eissn") if (c := canon_issn(ids.get(k, "")))})


def id_kinds(meta: dict | None) -> list[str]:
    return sorted({"issn" if k == "eissn" else k for k in ids_of(meta) if k in (*ID_KINDS, "eissn")})
