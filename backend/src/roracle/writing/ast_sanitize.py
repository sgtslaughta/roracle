"""Pandoc JSON AST sanitizer (draft content is untrusted).

Allow-list rewrite: raw HTML/TeX dropped, images only as inline PNG/JPEG/GIF/WebP data URIs, links only
http(s)/mailto/#fragment, attributes reduced to id + classes, document metadata discarded, citations
replaced by text, and a References section appended."""

import re
from dataclasses import dataclass, field
from typing import Any

_DATA_IMG = re.compile(r"data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]*", re.ASCII)
_LINK_OK = re.compile(r"(?:https?:|mailto:|#)", re.IGNORECASE)
_IDENT = re.compile(r"[A-Za-z0-9_:.\-]{1,100}", re.ASCII)
_RAW = {"RawBlock", "RawInline"}


@dataclass(frozen=True)
class Ref:
    key: str  # citation key, e.g. `doc:<uuid>`
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None


def _s(text: str) -> dict:
    return {"t": "Str", "c": text}


def words_inlines(text: str) -> list[dict]:
    out: list[dict] = []
    for i, w in enumerate(text.split()):
        if i:
            out.append({"t": "Space"})
        out.append(_s(w))
    return out


def _is_attr(x: Any) -> bool:
    return (
        isinstance(x, list)
        and len(x) == 3
        and isinstance(x[0], str)
        and isinstance(x[1], list)
        and all(isinstance(c, str) for c in x[1])
        and isinstance(x[2], list)
        and all(isinstance(kv, list) and len(kv) == 2 and all(isinstance(z, str) for z in kv) for kv in x[2])
    )


def _clean_attr(a: list) -> list:
    """Keep id and classes only: no style/on*/data-* key-values can reach the HTML."""
    ident = a[0] if _IDENT.fullmatch(a[0]) else ""
    return [ident, [c for c in a[1] if _IDENT.fullmatch(c)], []]


def _author_year(r: Ref) -> str:
    names = [a.split(",")[0].strip() if "," in a else a.split()[-1] for a in r.authors if a.strip()]
    who = names[0] if len(names) == 1 else f"{names[0]} and {names[1]}" if len(names) == 2 else None
    who = who or (f"{names[0]} et al." if names else (r.title[:30] or "Anon."))
    return f"{who} {r.year or 'n.d.'}"


class _Sanitizer:
    def __init__(self, refs: list[Ref], style: str):
        self.refs = {r.key: r for r in refs}
        self.style = style
        self.order: list[str] = []  # cited keys, first-use order

    def cite(self, c: list) -> list[dict]:
        parts: list[str] = []
        lit = True  # only `@fig:x`-style cross-references: keep their text
        for ci in c[0]:
            key = str(ci.get("citationId", ""))
            if (r := self.refs.get(key)) is not None:
                lit = False
                if key not in self.order:
                    self.order.append(key)
                parts.append(str(self.order.index(key) + 1) if self.style == "numeric" else _author_year(r))
            elif key.startswith(("doc:", "psg:")):
                lit = False
                parts.append("?")
            else:
                parts.append(key)
        if lit:
            return words_inlines("; ".join(parts))
        if self.style == "numeric":
            return words_inlines(f"[{', '.join(parts)}]")
        return words_inlines("[?]" if set(parts) == {"?"} else f"({'; '.join(parts)})")

    def walk(self, x: Any) -> Any:
        if _is_attr(x):
            return _clean_attr(x)
        if isinstance(x, list):
            out = []
            for i in x:
                if isinstance(i, dict) and i.get("t") in _RAW:
                    continue
                r = self.node(i)
                out.extend(r) if isinstance(i, dict) and i.get("t") in (
                    "Cite",
                    "Link",
                    "Image",
                ) else out.append(r)
            return out
        if isinstance(x, dict):
            return {k: self.walk(v) for k, v in x.items()}
        return x

    def node(self, n: Any) -> Any:
        """Inline/block node; Cite/Link/Image return a list of inlines to splice in place."""
        if not isinstance(n, dict):
            return self.walk(n)
        t, c = n.get("t"), n.get("c")
        if t == "Cite":
            return self.cite(c)
        if t == "Link":
            if _LINK_OK.match(c[2][0]):
                return [{"t": "Link", "c": [_clean_attr(c[0]), self.walk(c[1]), [c[2][0], c[2][1]]]}]
            return self.walk(c[1])
        if t == "Image":
            if _DATA_IMG.fullmatch(c[2][0]):
                return [{"t": "Image", "c": [_clean_attr(c[0]), self.walk(c[1]), [c[2][0], c[2][1]]]}]
            return self.walk(c[1])
        return self.walk(n)


def sanitize(ast: dict, refs: list[Ref], citation_style: str = "numeric") -> dict:
    """Return a new AST: safe content only, empty metadata, References appended."""
    s = _Sanitizer(refs, citation_style)
    blocks = s.walk(ast.get("blocks", []))
    if s.order:
        blocks.append({"t": "Header", "c": [1, ["references", [], []], [_s("References")]]})
        for i, key in enumerate(s.order, 1):
            r = s.refs[key]
            who = ", ".join(r.authors[:6]) + (" et al." if len(r.authors) > 6 else "")
            line = ". ".join(x for x in (who, str(r.year) if r.year else "", r.title or key) if x)
            lead = f"[{i}] " if citation_style == "numeric" else f"{_author_year(r)}. "
            blocks.append({"t": "Para", "c": words_inlines(lead + line)})
    return {"pandoc-api-version": ast.get("pandoc-api-version", [1, 23]), "meta": {}, "blocks": blocks}
