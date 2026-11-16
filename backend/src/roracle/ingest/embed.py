"""OpenAI-spec embeddings (EmbeddingGemma prompt format)."""

from roracle.queue import NonRetryable


def doc_prompt(title: str, text: str) -> str:
    return f"title: {title or 'none'} | text: {text}"


def query_prompt(q: str) -> str:
    return f"task: search result | query: {q}"


def embed(client, model: str, texts: list[str], dims: int, batch: int = 32) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        part = texts[i : i + batch]
        resp = client.embeddings.create(model=model, input=part)
        data = resp.data
        if len(data) != len(part):
            raise RuntimeError("embedding response count mismatch")
        if all(getattr(d, "index", None) is not None for d in data):
            data = sorted(data, key=lambda d: d.index)
        for d in data:
            if len(d.embedding) != dims:
                raise NonRetryable("embedding dimension mismatch")
            out.append(list(d.embedding))
    return out
