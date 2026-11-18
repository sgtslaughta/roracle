"""Summary intent, contentless-query detection and the lexical (BM25) query (pure regex, no I/O)."""

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


# Request scaffolding that only dilutes BM25 ("find things on robots" -> "robots"). A closed list of English words:
# anything not listed (every non-English word included) is kept, so stripping can never drop foreign content.
_FILLER_WORDS = """am any anything articles docs documents else find get give have has info information know let like list
    look looking material more need now our papers please regarding related search searching see show some
    something stuff tell there thing things too want we'd i'd i'm what's would could should also other"""
_FILLER = _STOP | frozenset(_FILLER_WORDS.split())
_EDGE = "\"'`.,;:!?()[]{}<>«»¿¡“”‘’"


def lexical_query(q: str) -> str:
    """`q` without English request filler, for the BM25 leg only (dense and rerank keep `q`). Words keep their
    order and spelling; if nothing is left, `q` itself."""
    kept = [w.strip(_EDGE) for w in q.split() if w.strip(_EDGE).casefold() not in _FILLER]
    return " ".join(w for w in kept if w) or q
