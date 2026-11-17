"""Deterministic offline models for `eval ci` (no GPU, no network): hashed bag-of-words embeddings and an
extractive answerer + word-overlap judge speaking the OpenAI client surface the pipeline uses."""

import hashlib
import json
import re
from types import SimpleNamespace

import numpy as np

DIMS = 768
_WORD = re.compile(r"\w+")


def _vec(text: str, dims: int) -> list[float]:
    v = np.zeros(dims)
    for tok in _WORD.findall(text.casefold()):
        h = hashlib.sha1(tok.encode()).digest()
        v[int.from_bytes(h[:4], "big") % dims] += 1.0 if h[4] & 1 else -1.0
    n = np.linalg.norm(v)
    if n == 0:
        v[0], n = 1.0, 1.0
    return (v / n).tolist()


class HashEmbedder:
    """`client.embeddings.create(model=, input=[...])` -> unit vectors from hashed word counts."""

    def __init__(self, dims: int = DIMS):
        self.dims = dims
        self.embeddings = SimpleNamespace(create=self._embed)

    def _embed(self, model: str, input: list[str]):
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=_vec(t, self.dims)) for i, t in enumerate(input)]
        )


def _reply(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class OverlapJudge:
    """`client.chat.completions.create(...)`: answers by quoting the first sentence of up to 3 passages with
    their `[n]`; judges a claim supported when most of its longer words occur in the passage."""

    THRESHOLD = 0.6

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

    def _chat(self, model: str, messages: list[dict], **_):
        body = messages[-1]["content"]
        if "<claim>" in body:
            passage = re.search(r"<passage>\n(.*?)\n</passage>", body, re.DOTALL)
            claim = re.search(r"<claim>\n(.*?)\n</claim>", body, re.DOTALL)
            words = {w for w in _WORD.findall(claim.group(1).casefold()) if len(w) > 3} if claim else set()
            have = set(_WORD.findall(passage.group(1).casefold())) if passage else set()
            ok = bool(words) and len(words & have) / len(words) >= self.THRESHOLD
            return _reply(json.dumps({"supported": ok}))
        parts = re.findall(r'<passage n="(\d+)">\n(.*?)\n</passage>', body, re.DOTALL)[:3]
        first = lambda t: re.split(r"(?<=[.!?])\s", " ".join(t.split()), maxsplit=1)[0]
        return _reply("\n".join(f"{first(t)} [{n}]" for n, t in parts) or "No passages.")


class CiLLM(HashEmbedder, OverlapJudge):
    """One client for retrieval (embeddings) and the judge (chat)."""

    def __init__(self, dims: int = DIMS):
        HashEmbedder.__init__(self, dims)
        OverlapJudge.__init__(self)
