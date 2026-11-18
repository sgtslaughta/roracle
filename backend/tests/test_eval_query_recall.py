"""Query-handling recall: the chat ranking must not lose documents when a question carries request filler.

Seeds tests/eval/query_recall.yaml into a throwaway ES index (offline hash embedder, no reranker) inside a
rolled-back Postgres transaction and computes doc-level recall@k over the chat path (`rank_for_chat`)."""

from pathlib import Path

import pytest
from sqlalchemy import select

from roracle import clients
from roracle.chat.retrieve import rank_for_chat
from roracle.eval import ci, fakes, fixture, gold
from roracle.models import Document

pytestmark = pytest.mark.integration
FIX = Path(__file__).parent / "eval" / "query_recall.yaml"
INDEX = "chunks-evalqr-1"
K = 8  # settings.chat_passages: the passages a chat answer can cite
ROBOTS = ("robots.bare", "robots.filler1", "robots.filler2", "robots.filler3")


def _recall(docs: list[str], relevant: set[str], k: int) -> float:
    return len(set(docs[:k]) & relevant) / len(relevant)


@pytest.fixture(scope="module")
def runs(settings, session_factory):  # session_factory: migrations at head
    # chat_min_similarity 0: hashed bag-of-words cosines sit under the 0.35 floor, which would silence the dense leg
    cs = ci.ci_settings(settings).model_copy(update={"embed_index": INDEX, "chat_min_similarity": 0})
    es, llm = clients.es(settings), fakes.CiLLM()
    out: dict[str, tuple[list[str], set[str]]] = {}
    try:
        with ci.sandbox(cs) as s:
            fixture.create_index(es, INDEX, fakes.DIMS)
            gs = fixture.seed(s, es, llm, cs, gold.load_file(FIX))
            ps = gold.resolve_principals(s, gs)
            for q in gs.queries:
                hits = rank_for_chat(s, es, llm, cs, ps[q.principals[0]], q.text, depth=cs.chat_candidates)
                docs = list(dict.fromkeys(h.doc_id for h in hits[:K]))  # docs of the citable passages
                if q.key.startswith("robots"):  # visible with -s
                    print(f"\n{q.key}: " + " | ".join(h.title[:14] for h in hits[:K]))
                out[q.key] = (docs, {g.doc for g in q.relevant})
            title = "Scalability in Computing and Robotics"
            out["_s01"] = ([str(s.scalar(select(Document.id).where(Document.title == title)))], set())
    finally:
        es.indices.delete(index=INDEX, ignore_unavailable=True)
    return out


def _summary(runs) -> dict[str, float]:
    by: dict[str, list[float]] = {}
    for key, (docs, rel) in runs.items():
        if not key.startswith("_"):
            variant = key.split(".")[1].rstrip("0123456789")
            by.setdefault(variant, []).append(_recall(docs, rel, len(docs)))
    return {v: sum(xs) / len(xs) for v, xs in sorted(by.items())}


def test_robots_filler_variants_cite_the_robotics_paper(runs):
    s01 = runs["_s01"][0][0]
    assert s01 in runs["robots.bare"][0]  # the bare query finds it ...
    missed = [k for k in ROBOTS if s01 not in runs[k][0]]
    assert missed == []  # ... and so must every filler variant


def test_filler_recall_matches_bare(runs):
    rec = _summary(runs)
    print(f"\nrecall@{K} by variant: " + ", ".join(f"{v}={r:.3f}" for v, r in rec.items()))
    assert rec["bare"] == 1.0
    assert rec["filler"] >= rec["bare"] and rec["follow"] >= rec["bare"]
    assert rec["tag"] == 1.0
