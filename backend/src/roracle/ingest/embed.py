"""OpenAI-spec embeddings (EmbeddingGemma prompt format)."""

import hashlib

import numpy as np

from roracle.queue import NonRetryable


class EmbedError(RuntimeError):
    pass


def doc_prompt(title: str, text: str, summary: str | None = None) -> str:
    if summary and summary.strip():
        return f"title: {title or 'none'} | text: {summary}\n\n{text}"
    return f"title: {title or 'none'} | text: {text}"


def prompt_sha(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


def query_prompt(q: str) -> str:
    return f"task: search result | query: {q}"


def embed(client, model: str, texts: list[str], dims: int, batch: int = 32) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        part = texts[i : i + batch]
        resp = client.embeddings.create(model=model, input=part)
        data = resp.data
        if len(data) != len(part):
            raise EmbedError("embedding response count mismatch")
        if all(getattr(d, "index", None) is not None for d in data):
            data = sorted(data, key=lambda d: d.index)
        for d in data:
            if len(d.embedding) != dims:
                raise NonRetryable("embedding dimension mismatch")
            out.append(list(d.embedding))
    return out


def to_bytes(vec) -> bytes:
    return np.asarray(vec, dtype="<f4").tobytes()


def from_bytes(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype="<f4")
