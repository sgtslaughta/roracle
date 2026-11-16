import os
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from roracle.ingest.embed import doc_prompt, embed, query_prompt
from roracle.queue import NonRetryable


class Fake:
    def __init__(self, dims=4):
        self.calls = []
        self.dims = dims
        self.embeddings = NS(create=self._create)

    def _create(self, model, input):
        self.calls.append(list(input))
        return NS(data=[NS(embedding=[float(t)] * self.dims) for t in input])


def test_batches_and_order():
    c = Fake()
    out = embed(c, "m", [str(i) for i in range(70)], 4)
    assert [len(x) for x in c.calls] == [32, 32, 6]
    assert [v[0] for v in out] == [float(i) for i in range(70)]


def test_empty_input():
    c = Fake()
    assert embed(c, "m", [], 4) == []
    assert c.calls == []


def test_count_mismatch():
    c = NS(embeddings=NS(create=lambda **k: NS(data=[NS(embedding=[1.0] * 4)])))
    with pytest.raises(RuntimeError, match="count mismatch"):
        embed(c, "m", ["a", "b"], 4)


def test_index_realigned():
    data = [NS(index=1, embedding=[2.0] * 4), NS(index=0, embedding=[1.0] * 4)]
    c = NS(embeddings=NS(create=lambda **k: NS(data=data)))
    assert [v[0] for v in embed(c, "m", ["a", "b"], 4)] == [1.0, 2.0]


def test_dim_mismatch():
    with pytest.raises(NonRetryable, match="embedding dimension mismatch"):
        embed(Fake(3), "m", ["1"], 4)


def test_transport_error_propagates():
    c = NS(
        embeddings=NS(
            create=lambda **k: (_ for _ in ()).throw(
                openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
            )
        )
    )
    with pytest.raises(openai.APIConnectionError):
        embed(c, "m", ["a"], 4)


def test_prompts():
    assert doc_prompt("T", "x") == "title: T | text: x"
    assert doc_prompt("", "x") == "title: none | text: x"
    assert query_prompt("q") == "task: search result | query: q"


@pytest.mark.integration
def test_real_endpoint():
    url = os.environ.get("ROR_LLM_BASE_URL", "http://localhost:21434/v1")
    client = openai.OpenAI(base_url=url, api_key="unused", max_retries=0, timeout=30)
    try:
        out = embed(client, "embeddinggemma", [doc_prompt("T", "a"), doc_prompt("T", "b")], 768)
    except openai.APIConnectionError:
        pytest.skip("embedding endpoint unreachable")
    assert len(out) == 2 and all(len(v) == 768 for v in out)
