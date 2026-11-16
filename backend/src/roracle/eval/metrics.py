"""Pure scoring: quote-anchored relevance, recall/MRR/nDCG at passage and document level, aggregation."""

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from statistics import fmean

from roracle.eval.gold import GoldItem

DEPTH = 100
KS = (5, 10, 50, 100)


def norm(s: str) -> str:
    """Casefold, join hyphenated line breaks, collapse whitespace."""
    return " ".join(re.sub(r"-\n", "", s.casefold()).split())


@dataclass
class Hit:
    doc_id: str
    text: str
    pages: list[int]


@dataclass
class PairResult:
    query_key: str
    principal: str
    kind: str
    scores: dict[str, float] | None  # None: no gold doc visible to this principal (empty R)
    acl_violations: int
    es_acl_violations: int
    latency_ms: int


def _matches(h: Hit, g: GoldItem) -> bool:
    if h.doc_id != g.doc:
        return False
    if g.quote:
        return norm(g.quote) in norm(h.text)
    if g.page:
        return g.page in h.pages
    return True


def match_ranks(hits: list[Hit], gold: list[GoldItem]) -> list[tuple[int, int]]:
    """(rank, grade) per credited gold, in rank order. Each gold is credited once, at its first matching
    hit; a hit credits at most one gold (its best-graded uncredited match) so nDCG stays within [0, 1]."""
    open_ = list(gold)
    out: list[tuple[int, int]] = []
    for rank, h in enumerate(hits[:DEPTH], 1):
        m = [g for g in open_ if _matches(h, g)]
        if m:
            g = max(m, key=lambda x: x.grade)  # max keeps the first of equal grades
            open_.remove(g)
            out.append((rank, g.grade))
    return out


def _dcg(pairs: list[tuple[int, int]], k: int) -> float:
    return sum((2**g - 1) / math.log2(r + 1) for r, g in pairs if r <= k)


def _rank_scores(credited: list[tuple[int, int]], grades: list[int], prefix: str) -> dict[str, float]:
    n = len(grades)
    out = {f"{prefix}_recall@{k}": sum(r <= k for r, _ in credited) / n for k in KS}
    out[f"{prefix}_mrr"] = 1 / credited[0][0] if credited else 0.0
    ideal = [(i, g) for i, g in enumerate(sorted(grades, reverse=True), 1)]
    out[f"{prefix}_ndcg@10"] = _dcg(credited, 10) / _dcg(ideal, 10)
    return out


def score_pair(hits: list[Hit], gold: list[GoldItem]) -> dict[str, float]:
    """All passage (`p_`) and document (`d_`) metrics for one (query, principal); `gold` is the visible set R."""
    if not gold:
        raise ValueError("empty gold")
    out = _rank_scores(match_ranks(hits, gold), [g.grade for g in gold], "p")
    best: dict[str, int] = {}
    for g in gold:
        best[g.doc] = max(best.get(g.doc, 0), g.grade)
    docs = list(dict.fromkeys(h.doc_id for h in hits[:DEPTH]))
    credited = [(r, best[d]) for r, d in enumerate(docs, 1) if d in best]
    return out | _rank_scores(credited, list(best.values()), "d")


def _pct(xs: list[int], q: float) -> int:
    xs = sorted(xs)
    return xs[max(0, math.ceil(q * len(xs)) - 1)] if xs else 0


def _mean(rows: list[PairResult]) -> dict[str, float]:
    sc = [r.scores for r in rows if r.scores is not None]
    return {k: fmean(s[k] for s in sc) for k in sc[0]} | {"pairs": len(sc)} if sc else {"pairs": 0}


def aggregate(rows: list[PairResult]) -> dict:
    """Macro means over (query, principal) pairs with non-empty R; empty-R pairs are only counted."""
    by_kind, by_principal = defaultdict(list), defaultdict(list)
    for r in rows:
        by_kind[r.kind].append(r)
        by_principal[r.principal].append(r)
    lat = [r.latency_ms for r in rows]
    return {
        "overall": _mean(rows),
        "by_kind": {k: _mean(v) for k, v in sorted(by_kind.items())},
        "by_principal": {k: _mean(v) for k, v in sorted(by_principal.items())},
        "empty_pairs": sum(r.scores is None for r in rows),
        "acl_violations": sum(r.acl_violations for r in rows),
        "es_acl_violations": sum(r.es_acl_violations for r in rows),
        "latency_p50_ms": _pct(lat, 0.5),
        "latency_p95_ms": _pct(lat, 0.95),
    }
