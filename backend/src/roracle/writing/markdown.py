"""Draft Markdown parser (regex, no deps). Content is untrusted: code is masked, nothing is evaluated.

Masking keeps every offset and newline, so line numbers stay valid across the derived texts:
`nocode` (fences/inline code blanked), `prose` (plus math blanked), `plain` (plus citations, labels, images, URLs)."""

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Any

import yaml

XREF = ("fig", "tbl", "sec", "eq")
_FM = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$", re.DOTALL | re.MULTILINE)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?(?:^ {0,3}\1[`~]*[ \t]*$|\Z)", re.DOTALL | re.MULTILINE)
_CODE = re.compile(r"``[^\n]*?``|`[^`\n]*`")
_DISPLAY = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_INLINE = re.compile(r"(?<![\\$])\$(?![\s$])[^$\n]+?(?<![\s\\])\$(?![\d$])")
_HEAD = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_LABEL = re.compile(r"\{#((?:fig|tbl|sec|eq):[\w-]+(?:\.[\w-]+)*)[^}\n]*\}")
_REF = re.compile(r"(?<![\w@])@((?:fig|tbl|sec|eq):[\w-]+(?:\.[\w-]+)*)")
_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(([^)\n]*)\)(?:\{#((?:fig|tbl):[\w-]+(?:\.[\w-]+)*)[^}\n]*\})?")
_BRACKET = re.compile(r"\[[^\[\]]*@[^\[\]]*\]")
_KEY = re.compile(r"(?<![\w@])@([\w:.#$%&\-+?<>~/]+)")
_BARE = re.compile(r"(?<![\w@])@((?:doc|psg):[\w:.\-]+)")
_URL = re.compile(r"https?://\S+|www\.\S+")
_SENT = re.compile(r"[.!?][\"')\]]*[ \t]+(?=[A-Z])|\n[ \t]*\n|\n(?=[ \t]*#)")
_WORD = re.compile(r"[^\W_][\w'’-]*")
_NUM = re.compile(r"^\d+(?:\.\d+)*\.?\s+")


@dataclass(frozen=True)
class Section:
    level: int
    title: str
    line: int
    text: str  # body up to the next heading of the same or higher level
    span: tuple[int, int] = (0, 0)  # offsets of `text`


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
        s = (self._nocode if raw else self.prose)[self._bounds[i] : end]
        return " ".join(_BRACKET.sub("", s).split())


def _blank(m: re.Match) -> str:
    return re.sub(r"[^\n]", " ", m.group())


def norm_title(t: str) -> str:
    return _NUM.sub("", t.strip()).strip().lower()


def words(text: str) -> int:
    return len(_WORD.findall(text))


def _sections(prose: str, p: Parsed) -> None:
    heads = list(_HEAD.finditer(prose))
    for i, m in enumerate(heads):
        level = len(m.group(1))
        end = len(prose)
        for n in heads[i + 1 :]:
            if len(n.group(1)) <= level:
                end = n.start()
                break
        title = _LABEL.sub("", m.group(2)).strip()
        p.sections.append(Section(level, title, p.line_at(m.start()), prose[m.end() : end], (m.end(), end)))


def _cites(p: Parsed) -> None:
    prose, spans = p.prose, []
    for b in _BRACKET.finditer(prose):
        spans.append(b.span())
        for k in _KEY.finditer(b.group()):
            _add_cite(p, k.group(1), b.start() + k.start())
    gap = prose
    for s, e in spans:
        gap = gap[:s] + " " * (e - s) + gap[e:]
    for k in _BARE.finditer(gap):
        _add_cite(p, k.group(1), k.start())


def _add_cite(p: Parsed, raw: str, pos: int) -> None:
    key = raw.rstrip(".,;:?!")
    if key.split(":", 1)[0] in XREF:
        return
    p.citations.append(Cite(key, p.sentence_at(pos, raw=True), p.line_at(pos)))


def parse(md: str) -> Parsed:
    p = Parsed()
    text = md.replace("\r\n", "\n").replace("\r", "\n")
    if (fm := _FM.match(text)) is not None:
        try:
            data = yaml.safe_load(fm.group(1))
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
    p._nl = [i for i, c in enumerate(nocode) if c == "\n"]
    for m in _DISPLAY.finditer(nocode):
        p.math.append(Math(m.group()[2:-2].strip(), p.line_at(m.start()), m.start(), True))
    nodisp = _DISPLAY.sub(_blank, nocode)
    for m in _INLINE.finditer(nodisp):
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
    body = plain
    for s in p.sections:
        if norm_title(s.title) in ("references", "bibliography"):
            body = body[: s.span[0]] + " " * (s.span[1] - s.span[0]) + body[s.span[1] :]
    p.words = words(body)
    ab = p.meta.get("abstract")
    if isinstance(ab, str):
        p.abstract_words = words(ab)
    else:
        sec = next((s for s in p.sections if norm_title(s.title) == "abstract"), None)
        p.abstract_words = words(_BRACKET.sub("", sec.text)) if sec else 0
    return p
