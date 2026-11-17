"""P13 T7: query router (heuristic hot path, LLM only on ambiguity behind `router_llm`)."""

import json
import statistics
import time
from types import SimpleNamespace

import pytest

from roracle.chat import router
from roracle.config import Settings

LONG = "what do the experiments in these studies say about how that approach behaves under noise"


class LLM:
    def __init__(self, reply=None, exc=None):
        self.reply, self.exc, self.calls = reply, exc, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


def s(**kw):
    return Settings(router=True, **kw)


@pytest.mark.parametrize(
    ("q", "route"),
    [
        ("What is the batch size used?", "fact"),
        ("What are the main themes across all the documents?", "global"),
        ("Summarize everything in the whole library", "global"),
        ("Which papers discuss contrastive learning?", "global"),
        ("What themes recur in my collection?", "global"),
        ("What do all the papers say about dropout", "global"),
        ("Give me an overview across my library", "global"),
        ("What does this paper say about my collection", "fact"),
        ("Compare BM25 and dense retrieval on recall", "multihop"),
        ("How does pruning affect accuracy on small models", "multihop"),
    ],
)
def test_heuristic_routes_without_llm(q, route):
    llm = LLM('{"route": "fact"}')
    assert router.classify(llm, s(router_llm=True), q) == route
    assert llm.calls == []  # strong cue / short question: no model call


def test_ambiguous_without_router_llm_is_fact_and_never_calls():
    llm = LLM('{"route": "global"}')
    assert router.classify(llm, s(), LONG) == "fact" and llm.calls == []


def test_ambiguous_uses_llm_with_only_the_question():
    llm = LLM('noise {"route": "global"} noise')
    assert router.classify(llm, s(router_llm=True), LONG) == "global"
    (kw,) = llm.calls
    assert kw["temperature"] == 0 and kw["timeout"] == 1.5
    assert json.dumps(kw["messages"]).count(LONG) == 1 and len(kw["messages"]) == 2


@pytest.mark.parametrize(
    "reply",
    ["not json", "{}", '{"route": "other"}', '{"route": 3}', "[1]", "", None, '{"route": "global"'],
)
def test_invalid_router_json_is_fact(reply):
    assert router.classify(LLM(reply), s(router_llm=True), LONG) == "fact"


@pytest.mark.parametrize("exc", [TimeoutError("slow"), RuntimeError("down")])
def test_router_error_or_timeout_is_fact(exc):
    assert router.classify(LLM(exc=exc), s(router_llm=True), LONG) == "fact"


def test_heuristic_overhead_is_negligible():
    """Hot path = regexes only. Budget: p95 well under 1 ms per question (plan: router call p95 <= 1500 ms)."""
    qs = [
        LONG,
        "What is the learning rate?",
        "Compare A and B",
        "x " * 200,
        "main themes across all the documents",
    ]
    ts, cfg = [], s()
    for i in range(2000):
        t = time.perf_counter()
        router.classify(None, cfg, qs[i % len(qs)])
        ts.append((time.perf_counter() - t) * 1000)
    ts.sort()
    p50, p95 = statistics.median(ts), ts[int(len(ts) * 0.95)]
    print(f"router heuristic overhead p50={p50:.4f} ms p95={p95:.4f} ms")
    assert p95 < 1.0


@pytest.mark.parametrize(
    "q",
    [
        "What are the main findings of the Smith 2021 paper?",
        "What are the key findings of Smith et al. 2021 across the experiments?",
        "key findings of this study on vitamin D",
        "What is the main topic of the document?",
        "What are the main themes of the paper in the whole library of results?",
        'Summarize the main findings of "Attention Is All You Need" across all the papers',
    ],
)
def test_single_document_questions_never_go_global(q):
    llm = LLM('{"route": "global"}')
    assert router.classify(llm, s(router_llm=True), q) in ("fact", "multihop") and llm.calls == []


@pytest.mark.parametrize(
    "q",
    ["What are the main themes across all the documents?", "Which papers discuss contrastive learning?",
     "What are the key findings across all papers on sleep?", "Summarize everything in the whole library"],
)  # fmt: skip
def test_library_questions_still_go_global(q):
    assert router.classify(None, s(), q) == "global"


@pytest.mark.parametrize(
    "q",
    [
        "In 2020, what were the main themes across all the documents?",
        "Since 2015 which papers discuss attention across all the papers?",
        'Which papers discuss "transformer attention" across all the papers?',
        "Which papers discuss the trial design across all the papers?",
    ],
)
def test_veto_does_not_over_match(q):
    assert router.classify(None, s(), q) == "global"
