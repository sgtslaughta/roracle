import hashlib
import os
from types import SimpleNamespace
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from roracle.ingest.embed import (
    EmbedUnavailable,
    doc_prompt,
    embed,
    from_bytes,
    prompt_sha,
    query_prompt,
    to_bytes,
)
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
    with pytest.raises(EmbedUnavailable):  # the query path: no retries, a fast 503 upstream
        embed(c, "m", ["a"], 4)


def test_prompts():
    assert doc_prompt("T", "x") == "title: T | text: x"
    assert doc_prompt("", "x") == "title: none | text: x"
    assert doc_prompt("T", "x", "S") == "title: T | text: S\n\nx"
    assert doc_prompt("", "x", "  ") == "title: none | text: x"
    assert doc_prompt("T", "x", None) == "title: T | text: x"
    assert prompt_sha("a") == hashlib.sha256(b"v2\na").hexdigest()  # version-prefixed (R8)
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


def test_bytes_round_trip():
    v = [0.5, -1.25, 3.0]
    assert list(from_bytes(to_bytes(v))) == v
    assert len(to_bytes(v)) == 12


# ---- task 10.11 fix round (I3): retries, throttle, cancel on first hard error -------------------------
class _Status(Exception):
    def __init__(self, code, retry_after=None):
        super().__init__(str(code))
        self.status_code = code
        self.response = SimpleNamespace(headers={"retry-after": str(retry_after)} if retry_after else {})


def _client(script):
    """`script(call_no, input)` returns a list of vectors or raises."""
    calls = []

    def create(model, input):
        calls.append(list(input))
        out = script(len(calls), input)
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=v) for i, v in enumerate(out)])

    return SimpleNamespace(embeddings=SimpleNamespace(create=create)), calls


@pytest.fixture
def sleeps(monkeypatch):
    from roracle.ingest import embed as emb

    seen = []
    monkeypatch.setattr(emb, "_sleep", seen.append)
    monkeypatch.setitem(emb._pause, "until", 0.0)
    return seen


def test_429_is_retried_honouring_retry_after_then_succeeds(sleeps):
    def script(n, inp):
        if n == 1:
            raise _Status(429, retry_after=3)
        return [[1.0]] * len(inp)

    client, calls = _client(script)
    assert embed(client, "m", ["a", "b"], 1, retries=4) == [[1.0], [1.0]]
    assert len(calls) == 2 and 3.0 <= max(sleeps) <= 3.0 * 1.25 + 0.01


def test_5xx_backs_off_exponentially_and_gives_up_after_the_retry_budget(sleeps):
    from roracle.ingest import embed as emb

    client, calls = _client(lambda n, inp: (_ for _ in ()).throw(_Status(503)))
    with pytest.raises(_Status):
        embed(client, "m", ["a"], 1, retries=emb.BATCH_RETRIES)
    assert len(calls) == emb.BATCH_RETRIES + 1
    assert sleeps == sorted(sleeps) and len(sleeps) == emb.BATCH_RETRIES  # growing waits


def test_a_client_error_is_not_retried(sleeps):
    client, calls = _client(lambda n, inp: (_ for _ in ()).throw(_Status(400)))
    with pytest.raises(_Status):
        embed(client, "m", ["a"], 1, retries=4)
    assert len(calls) == 1 and sleeps == []


def test_first_hard_error_cancels_the_requests_not_yet_started(sleeps):
    import threading

    gate = threading.Event()

    def script(n, inp):
        if inp[0] == "t0":
            raise _Status(400)
        gate.wait(0.2)  # the other workers are busy while the failure lands
        return [[1.0]] * len(inp)

    client, calls = _client(script)
    texts = [f"t{i}" for i in range(32 * 40)]  # 40 requests, 4 workers
    with pytest.raises(_Status):
        embed(client, "m", texts, 1, batch=32, concurrency=4)
    assert len(calls) < 12  # nowhere near 40: queued requests were cancelled


# ---- task 10.11 fix round 2 (RI-1): the query path never retries or pauses ---------------------------
def test_query_path_does_not_retry_sleep_or_pause_other_threads(sleeps):
    import threading
    import time

    from roracle.ingest import embed as emb

    client, calls = _client(lambda n, inp: (_ for _ in ()).throw(_Status(429, retry_after=30)))
    t = time.monotonic()
    with pytest.raises(emb.EmbedUnavailable):
        embed(client, "m", ["q"], 1)  # default retries=0: the interactive path
    assert time.monotonic() - t < 0.5 and len(calls) == 1 and sleeps == []
    assert emb._pause["until"] == 0.0  # nothing is shared with other threads
    ok, _ = _client(lambda n, inp: [[1.0]] * len(inp))
    res, t = [], time.monotonic()
    th = threading.Thread(target=lambda: res.append(embed(ok, "m", ["x"], 1)))
    th.start()
    th.join(2)
    assert res == [[[1.0]]] and time.monotonic() - t < 1  # another query is not stalled


def test_batch_retry_after_is_capped(sleeps):
    from roracle.ingest import embed as emb

    def script(n, inp):
        if n == 1:
            raise _Status(429, retry_after=3600)
        return [[1.0]] * len(inp)

    client, _ = _client(script)
    embed(client, "m", ["a"], 1, retries=emb.BATCH_RETRIES)
    assert max(sleeps) <= emb._RETRY_AFTER_MAX_S * 1.25 + 0.01
