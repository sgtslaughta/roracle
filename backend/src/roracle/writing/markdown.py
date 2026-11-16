"""Draft Markdown parser (regex, no deps). Content is untrusted: code is masked, nothing is evaluated.

Masking keeps every offset and newline, so line numbers stay valid across the derived texts:
`nocode` (fences/inline code blanked), `prose` (plus math blanked), `plain` (plus citations, labels, images, URLs)."""

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from itertools import islice
from typing import Any

import yaml

XREF = ("fig", "tbl", "sec", "eq")
_FM = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$", re.DOTALL | re.MULTILINE)
_FENCE = re.compile(
    r"^ {0,3}(`{3,}+|~{3,}+)[^\n]*\n.*?(?:^ {0,3}\1[`~]*[ \t]*$|\Z)", re.DOTALL | re.MULTILINE
)
_CODE = re.compile(r"``[^\n]{0,1000}?``|`[^`\n]{0,1000}`")
_DISPLAY = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_INLINE = re.compile(r"(?<![\\$])\$(?![\s$])[^$\n]+?(?<![\s\\])\$(?![\d$])")
_HEAD = re.compile(r"^ {0,3}(#{1,6})[ \t]+([^\n]*)$", re.MULTILINE)  # title trimmed in Python (linear)
_KEYP = r"[\w-]++(?:\.[\w-]++)*+"  # possessive: no backtracking on failure
_LABEL = re.compile(rf"\{{#((?:fig|tbl|sec|eq):{_KEYP})[^{{}}\n]{{0,200}}\}}")
_REF = re.compile(r"(?<![\w@])@((?:fig|tbl|sec|eq):[\w-]+(?:\.[\w-]+)*)")
_IMAGE = re.compile(
    rf"!\[([^\[\]\n]{{0,500}})\]\(([^()\n]{{0,2000}})\)(?:\{{#((?:fig|tbl):{_KEYP})[^{{}}\n]{{0,200}}\}})?"
)
_BRACKET = re.compile(r"\[[^\[\]@]*@[^\[\]]*\]")
_KEY = re.compile(r"(?<![\w@])@([\w:.#$%&\-+?<>~/]+)")
_BARE = re.compile(r"(?<![\w@])@((?:doc|psg):[\w:.\-]+)")
_URL = re.compile(r"https?://\S+|www\.\S+")
_SENT = re.compile(r"[.!?][\"')\]]*[ \t]++(?=[A-Z])|\n[ \t]*\n|\n(?=[ \t]*#)")
_WORD = re.compile(r"[^\W_][\w'’-]*")
_NUM = re.compile(r"^\d+(?:\.\d+)*\.?\s+")
WINDOW = 1500  # max chars of a sentence kept around a position
TITLE_MAX = 300
SECTIONS_MAX = 5000  # headings beyond this are ignored
ITEMS_MAX = 20000  # citations / inline math kept (the rest are ignored; the citation cap rejects such drafts)
FM_MAX = 64 * 1024
_ALIAS = re.compile(r"(?:^|[\s\[{,:-])[&*][\w-]")


@dataclass(frozen=True)
class Section:
    level: int
    title: str
    line: int
    span: tuple[int, int]  # offsets in `prose` of the body up to the next heading of the same or higher level


@dataclass(frozen=True)
class Cite:
    key: str
    sentence: str
    line: int


@dataclass(frozen=True)
class Figure:
    alt: str
    src: str
    label: str | None
    line: int


@dataclass(frozen=True)
class Math:
    expr: str
    line: int
    pos: int
    display: bool


@dataclass
class Parsed:
    meta: dict[str, Any] = field(default_factory=dict)
    meta_issue: str | None = None
    sections: list[Section] = field(default_factory=list)
    citations: list[Cite] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    labels: set[str] = field(default_factory=set)
    label_defs: list[tuple[str, int]] = field(default_factory=list)
    refs: list[tuple[str, int]] = field(default_factory=list)
    math: list[Math] = field(default_factory=list)
    words: int = 0
    abstract_words: int = 0
    prose: str = ""
    plain: str = ""
    _bounds: list[int] = field(default_factory=lambda: [0])
    _nl: list[int] = field(default_factory=list)
    _nocode: str = ""

    def line_at(self, pos: int) -> int:
        return bisect_left(self._nl, pos) + 1

    def sentence_at(self, pos: int, *, raw: bool = False) -> str:
        i = bisect_right(self._bounds, pos) - 1
        end = self._bounds[i + 1] if i + 1 < len(self._bounds) else len(self.prose)
        lo, hi = max(self._bounds[i], pos - WINDOW), min(end, pos + WINDOW)
        return " ".join(_BRACKET.sub("", (self._nocode if raw else self.prose)[lo:hi]).split())


def _blank(m: re.Match) -> str:
    t = m.group()
    return " " * len(t) if "\n" not in t else re.sub(r"[^\n]", " ", t)


def blank_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """Blank (non-newline chars of) all spans in one pass."""
    out, last = [], 0
    for a, b in sorted(spans):
        a = max(a, last)
        if b <= a:
            continue
        out += [text[last:a], re.sub(r"[^\n]", " ", text[a:b])]
        last = b
    out.append(text[last:])
    return "".join(out)


def norm_title(t: str) -> str:
    return _NUM.sub("", t.strip()).strip().lower()


def words(text: str) -> int:
    return len(_WORD.findall(text))


def _sections(prose: str, p: Parsed) -> None:
    heads = list(islice(_HEAD.finditer(prose), SECTIONS_MAX))
    ends = [len(prose)] * len(heads)
    stack: list[int] = []  # open headings, increasing level
    for i, m in enumerate(heads):
        while stack and len(heads[stack[-1]].group(1)) >= len(m.group(1)):
            ends[stack.pop()] = m.start()
        stack.append(i)
    for m, end in zip(heads, ends, strict=True):
        title = _LABEL.sub("", m.group(2)[:TITLE_MAX]).rstrip().rstrip("#").strip()
        p.sections.append(Section(len(m.group(1)), title, p.line_at(m.start()), (m.end(), end)))


def _cites(p: Parsed) -> None:
    prose = p.prose
    for b in _BRACKET.finditer(prose):
        if len(p.citations) >= ITEMS_MAX:
            return
        for k in _KEY.finditer(b.group()):
            _add_cite(p, k.group(1), b.start() + k.start())
    for k in _BARE.finditer(_BRACKET.sub(_blank, prose)):
        if len(p.citations) >= ITEMS_MAX:
            return
        _add_cite(p, k.group(1), k.start())


SENTENCES_MAX = 2000  # cites past this get no sentence (the citation cap rejects such drafts anyway)


def _add_cite(p: Parsed, raw: str, pos: int) -> None:
    key = raw.rstrip(".,;:?!")
    if key.split(":", 1)[0] in XREF:
        return
    sent = p.sentence_at(pos, raw=True) if len(p.citations) < SENTENCES_MAX else ""
    p.citations.append(Cite(key, sent, p.line_at(pos)))


def parse(md: str) -> Parsed:
    p = Parsed()
    text = md.replace("\r\n", "\n").replace("\r", "\n")
    if (fm := _FM.match(text)) is not None:
        raw = fm.group(1)
        if len(raw) > FM_MAX:
            p.meta_issue = "front matter is too large"
        elif _ALIAS.search(raw):
            p.meta_issue = "front matter anchors and aliases are not allowed"
        else:
            try:
                data = yaml.safe_load(raw)
            except yaml.YAMLError:
                p.meta_issue = "front matter is not valid YAML"
            else:
                if isinstance(data, dict):
                    p.meta = data
                elif data is not None:
                    p.meta_issue = "front matter must be a YAML mapping"
        text = _blank(fm) + text[fm.end() :]
    nocode = _CODE.sub(_blank, _FENCE.sub(_blank, text))
    p._nocode = nocode
    p._nl = [m.start() for m in re.finditer("\n", nocode)]
    for m in islice(_DISPLAY.finditer(nocode), ITEMS_MAX):
        p.math.append(Math(m.group()[2:-2].strip(), p.line_at(m.start()), m.start(), True))
    nodisp = _DISPLAY.sub(_blank, nocode)
    for m in islice(_INLINE.finditer(nodisp), ITEMS_MAX):
        p.math.append(Math(m.group()[1:-1].strip(), p.line_at(m.start()), m.start(), False))
    p.math.sort(key=lambda x: x.pos)
    p.prose = _INLINE.sub(_blank, nodisp)
    p._bounds = [0] + [m.end() for m in _SENT.finditer(p.prose)]
    for m in _LABEL.finditer(p.prose):
        p.label_defs.append((m.group(1), p.line_at(m.start())))
    p.labels = {k for k, _ in p.label_defs}
    p.refs = [(m.group(1), p.line_at(m.start())) for m in _REF.finditer(p.prose)]
    for m in _IMAGE.finditer(p.prose):
        p.figures.append(Figure(m.group(1).strip(), m.group(2).strip(), m.group(3), p.line_at(m.start())))
    _sections(p.prose, p)
    _cites(p)
    plain = _BRACKET.sub(_blank, p.prose)
    for rx in (_LABEL, _IMAGE, _URL, _REF):
        plain = rx.sub(_blank, plain)
    p.plain = _HEAD.sub(_blank, plain)
    refs = [s.span for s in p.sections if norm_title(s.title) in ("references", "bibliography")]
    body = blank_spans(plain, refs)
    p.words = words(body)
    ab = p.meta.get("abstract")
    if isinstance(ab, str):
        p.abstract_words = words(ab)
    else:
        sec = next((s for s in p.sections if norm_title(s.title) == "abstract"), None)
        p.abstract_words = words(_BRACKET.sub("", p.prose[slice(*sec.span)])) if sec else 0
    return p
