"""OpenAI-spec embeddings (EmbeddingGemma prompt format)."""

from roracle.queue import NonRetryable


def doc_prompt(title: str, text: str) -> str:
    return f"title: {title or 'none'} | text: {text}"


def query_prompt(q: str) -> str:
    return f"task: search result | query: {q}"


def embed(client, model: str, texts: list[str], dims: int, batch: int = 32) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        resp = client.embeddings.create(model=model, input=texts[i : i + batch])
        for d in resp.data:
            if len(d.embedding) != dims:
                raise NonRetryable("embedding dimension mismatch")
            out.append(list(d.embedding))
    return out
