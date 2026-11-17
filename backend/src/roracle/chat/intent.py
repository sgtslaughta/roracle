"""Summary intent and contentless-query detection (pure regex, no I/O)."""

import re

_I = re.IGNORECASE
_INTENT = re.compile(
    r"\b(?:summari[sz]e|summary|summaries|overview|tl;?dr|abstract of|eli5"
    r"|key (?:findings?|points?|contributions?|takeaways?)"
    r"|main (?:ideas?|points?|contributions?|results?)"
    r"|what (?:is|'s) (?:this|it|the) (?:paper |article |study )?about"
    r"|explain (?:this|the) (?:paper|article|study)"
    r"|methods? used|limitations?"
    r"|explain for (?:a |an )?non-?specialists?)\b",
    _I,
)
# phrases removed before counting content tokens: intent words, politeness, deictics
_STRIP = re.compile(
    _INTENT.pattern
    + r"|\b(?:please|can you|could you|would you|give me|i want|i need|show me|tell me|what is|what's"
    r"|this|that|the paper|the article|the study|the document|document|article|paper|study|it"
    r"|key|main|findings?|points?|ideas?|results?|used|methods?|explain|for|non-?specialist)\b",
    _I,
)
_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "on",
        "for",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "me",
        "my",
        "you",
        "your",
        "i",
        "we",
        "us",
        "can",
        "do",
        "does",
        "about",
        "with",
        "from",
        "by",
        "as",
        "at",
        "into",
        "what",
        "how",
        "why",
        "who",
        "which",
    ]
)


def summary_intent(q: str) -> bool:
    return bool(_INTENT.search(q))


def contentless(q: str) -> bool:
    """True when almost nothing but request words remains (needs an open document to mean anything)."""
    rest = _STRIP.sub(" ", q)
    toks = [t for t in re.findall(r"\w+", rest.lower()) if t not in _STOP]
    return not toks
