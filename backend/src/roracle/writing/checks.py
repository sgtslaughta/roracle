"""Pure draft checks: `check_<rule>(parsed, profile) -> list[Finding]`. No I/O, no LLM."""

import re
from dataclasses import dataclass

from roracle.writing.cite_check import parse_key
from roracle.writing.markdown import Parsed, norm_title
from roracle.writing.profiles import Profile


@dataclass(frozen=True)
class Finding:
    rule: str
    message: str
    line: int | None = None
    soft: bool = False  # never blocking (e.g. style check unavailable); not reported

    def dict(self) -> dict:
        return {"rule": self.rule, "message": self.message, "line": self.line}


def check_structure(p: Parsed, prof: Profile) -> list[Finding]:
    have = {norm_title(s.title) for s in p.sections}
    return [
        Finding("structure", f'missing section "{t}"')
        for t in prof.required_sections
        if norm_title(t) not in have
    ]


def _empty(v: object) -> bool:
    return v is None or (isinstance(v, str | list | dict) and not (v.strip() if isinstance(v, str) else v))


def check_metadata(p: Parsed, prof: Profile) -> list[Finding]:
    out = [Finding("metadata", p.meta_issue, 1)] if p.meta_issue else []
    return out + [
        Finding("metadata", f'missing metadata "{k}"', 1)
        for k in prof.required_metadata
        if _empty(p.meta.get(k))
    ]


def check_limits(p: Parsed, prof: Profile) -> list[Finding]:
    lim, out = prof.limits, []
    title = p.meta.get("title")
    n_title = len(title) if isinstance(title, str) else 0
    for name, n, mx in (
        ("words", p.words, lim.words_max),
        ("abstract words", p.abstract_words, lim.abstract_words_max),
        ("title characters", n_title, lim.title_chars_max),
        ("figures", len(p.figures), lim.figures_max),
        ("references", len({c.key for c in p.citations}), lim.references_max),
    ):
        if mx is not None and n > mx:
            out.append(Finding("limits", f"{name}: {n} exceeds the limit of {mx}"))
    return out


_SYMBOL = re.compile(r"(?:[A-Za-z]|\\[A-Za-z]+)(?:_(?:\{\w+\}|\w))?(?:\^(?:\{\w+\}|\w))?")
MAX_FINDINGS = 50  # per rule for notation/acronyms
_CUE = re.compile(r"\b(?:let|where|denote[sd]?|defin(?:e|es|ed)|is the|are the|be the)\b", re.IGNORECASE)


def check_notation(p: Parsed, prof: Profile) -> list[Finding]:
    seen, out = set(), []
    for m in p.math:
        if m.display or not _SYMBOL.fullmatch(m.expr) or m.expr in seen:
            continue
        seen.add(m.expr)
        if not _CUE.search(p.sentence_at(m.pos)):
            out.append(Finding("notation", f"symbol ${m.expr}$ is used before it is defined", m.line))
            if len(out) >= MAX_FINDINGS:
                break
    return out


_ACR = re.compile(r"\b[A-Z][A-Z0-9]{1,6}s?\b")


_OPEN = re.compile(r"\s*\(")


def check_acronyms(p: Parsed, prof: Profile) -> list[Finding]:
    seen, out, allow = set(), [], set(prof.acronym_allowlist)
    text = p.plain
    for m in _ACR.finditer(text):
        a = m.group().removesuffix("s")
        if a in seen or a in allow or m.group() in allow:
            continue
        seen.add(a)
        j = m.start() - 1
        while j >= 0 and text[j].isspace():
            j -= 1
        if (j >= 0 and text[j] == "(") or _OPEN.match(text, m.end()) or text.startswith(")", m.end()):
            continue
        out.append(Finding("acronyms", f"acronym {a} is not defined at first use", p.line_at(m.start())))
        if len(out) >= MAX_FINDINGS:
            break
    return out


def check_crossrefs(p: Parsed, prof: Profile) -> list[Finding]:
    out, seen = [], set()
    for lab, line in p.label_defs:
        if lab in seen:
            out.append(Finding("crossrefs", f"duplicate label {lab}", line))
        seen.add(lab)
    out += [
        Finding("crossrefs", f"reference @{r} has no matching label", line)
        for r, line in p.refs
        if r not in p.labels
    ]
    return out


def check_alt_text(p: Parsed, prof: Profile) -> list[Finding]:
    return [Finding("alt_text", "image has no alt text", f.line) for f in p.figures if not f.alt]


_NON_SI = re.compile(
    r"(?<![\w.])\d+(?:\.\d+)?\s*(mph|kph|kmh|secs?|hrs?|mins|cc|gms?|lbs?|inch(?:es)?)\b|\d\s*°\s*F\b"
)
_GLUED = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(kg|mg|g|cm|mm|km|ms|Hz|kHz|MHz|GHz|Pa|mol|mL|W|V)\b")


def check_si_units(p: Parsed, prof: Profile) -> list[Finding]:
    out, seen = [], set()
    for rx, msg in (
        (_NON_SI, "non-SI or ambiguous unit"),
        (_GLUED, "put a space between the number and the unit"),
    ):
        for m in rx.finditer(p.plain):
            if m.group() not in seen:
                seen.add(m.group())
                out.append(Finding("si_units", f'{msg}: "{m.group()}"', p.line_at(m.start())))
    return sorted(out, key=lambda f: f.line or 0)


def check_ai_disclosure(p: Parsed, prof: Profile) -> list[Finding]:
    a = p.meta.get("ai_assistance")
    if not isinstance(a, dict):
        return [Finding("ai_disclosure", "front matter needs an ai_assistance block", 1)]
    if not isinstance(a.get("used"), bool):
        return [Finding("ai_disclosure", "ai_assistance.used must be true or false", 1)]
    if a["used"] and not (isinstance(a.get("description"), str) and a["description"].strip()):
        return [Finding("ai_disclosure", "ai_assistance.description is required when AI was used", 1)]
    return []


def check_citations(
    p: Parsed, prof: Profile, resolved: dict[str, object | None], limit: int = 200
) -> list[Finding]:
    """`resolved` = cite_check.resolve_keys for the draft's keys. Same wording for invisible and missing."""
    out, seen = [], set()
    for c in p.citations:
        if c.key in seen:
            continue
        if len(seen) >= limit:
            break
        seen.add(c.key)
        if parse_key(c.key) is None:
            out.append(
                Finding(
                    "citations",
                    f"citation {c.key[:80]} is malformed: use @doc:<uuid> or @psg:<uuid>:<n>",
                    c.line,
                )
            )
        elif resolved.get(c.key) is None:
            out.append(
                Finding(
                    "citations",
                    f"citation {c.key} does not resolve to a library document you can access",
                    c.line,
                )
            )
    return out
