"""Quote untrusted text (bodies, titles, authors) before it enters a prompt, so it cannot close or open our tags."""

import re

TAGS = (
    "passage", "note", "unit", "finding", "question", "found",
    "source", "document", "chunk", "title", "sentence", "claim", "paper", "vocab",
)  # fmt: skip  # every delimiter tag used in a prompt
_CLOSE = re.compile(r"<\s*/\s*([A-Za-z][\w-]*)")
_OPEN = re.compile(rf"<\s*({'|'.join(TAGS)})\b", re.IGNORECASE)


def quote(text: str) -> str:
    """Quoted data cannot close ANY tag (even `< /x >`) or open one of ours: `</` becomes `<\\/`, `<tag` becomes `<\\tag`."""
    text = _CLOSE.sub(lambda m: "<\\/" + m.group(1).lower(), text)
    return _OPEN.sub(lambda m: "<\\" + m.group(1).lower(), text)


def meta(text: str, limit: int = 300) -> str:
    """One-line, length-capped, quoted metadata (title, authors)."""
    return quote(" ".join(text.split())[:limit])
