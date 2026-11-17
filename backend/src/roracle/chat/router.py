"""Query router (R13): fact / multihop / global.

Hot path is a precompiled-regex heuristic (microseconds, no I/O). Only questions the heuristic cannot place and
that are long enough to be non-trivial reach the small-model call, and only when `router_llm` is on. Any error,
timeout or invalid JSON means `fact`. The prompt carries the question only: no document text.
"""

import json
import logging
import re
from typing import Literal

from roracle.config import Settings

log = logging.getLogger(__name__)
Route = Literal["fact", "multihop", "global"]
ROUTES = ("fact", "multihop", "global")
LLM_MIN_WORDS = 8
MAX_Q = 600
_GLOBAL = re.compile(
    r"\b(?:(?:main|key|major|common|recurring|overall) (?:themes?|trends?|topics?|findings|conclusions|takeaways)"
    r"|(?:across|throughout|among) (?:all|the|my|our) (?:the )?(?:documents?|papers?|library|corpus|collection|sources|files)"
    r"|(?:in|of|from) (?:all|the whole|the entire|the full) (?:the )?(?:documents?|papers?|library|corpus|collection)"
    r"|(?:summari[sz]e|overview of|survey of) (?:all|everything|the (?:whole|entire|full))"
    r"|which (?:documents|papers|sources) (?:discuss|mention|cover|address)"
    r"|how many (?:documents|papers))\b",
    re.IGNORECASE,
)
_MULTI = re.compile(
    r"\b(?:compare|contrast|difference between|differences between|relationship between|how (?:does|do|did) .{3,60} "
    r"(?:affect|influence|relate to|lead to)|and then|both .{2,40} and)\b",
    re.IGNORECASE,
)
_SINGLE = re.compile(  # a singular target: one named/deictic document is a fact question, never a library sweep
    r"\b(?i:(?:this|that|the|his|her|their|my|our|a|one)\s+(?:\w+\s+)?(?:paper|study|article|document|report|trial|"
    r"preprint|thesis|chapter|pdf|file|manuscript))\b"
    r"|\b[A-Z][a-z]+(?:\s+(?:et\s+al\.?|and\s+[A-Z][a-z]+))?,?\s+\(?(?:19|20)\d\d[a-z]?\)?"
    r"|[\"“«][^\"”»]{6,}[\"”»]"
)
SYSTEM = (
    'Classify the user question for a research library. Reply with JSON only: {"route": "fact"|"multihop"|"global"}. '
    "fact = answerable from a few passages; multihop = needs combining several separate facts or comparing "
    "specific items; global = asks for themes, trends or an overview across many documents."
)


def heuristic(question: str) -> Route | None:
    """`global`/`multihop` on a strong cue, `fact` for short questions, None when it cannot tell."""
    q = question[:MAX_Q]
    if _SINGLE.search(q):  # veto: never a global sweep, never worth an LLM call
        return "multihop" if _MULTI.search(q) else "fact"
    if _GLOBAL.search(q):
        return "global"
    if _MULTI.search(q):
        return "multihop"
    return "fact" if len(q.split()) < LLM_MIN_WORDS else None


def classify(llm, settings: Settings, question: str) -> Route:
    r = heuristic(question)
    if r is not None:
        return r
    if not settings.router_llm:
        return "fact"
    try:
        resp = llm.chat.completions.create(
            model=settings.router_model or settings.summary_model,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": question[:MAX_Q]}],
            temperature=0,
            max_tokens=24,
            timeout=settings.router_timeout_s,
        )
        text = resp.choices[0].message.content or ""
        data = json.loads(text[text.index("{") : text.rindex("}") + 1])
        route = data.get("route") if isinstance(data, dict) else None
    except Exception:
        log.warning("router fell back to fact", exc_info=True)
        return "fact"
    return route if route in ROUTES else "fact"
