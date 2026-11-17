"""Gain gate (plan constants): PASS only on a significant, safe improvement on a large enough reference set.

Deltas are candidate minus baseline over (query, principal) pairs present in both runs. Pairs of one query
are averaged first and the bootstrap resamples queries (a query's principals are correlated), so the
interval is not overconfident. FAIL beats INSUFFICIENT (ACL, recall and judge losses are firm at any size);
a missing gain on a too-small set is INSUFFICIENT, not FAIL.
"""

from dataclasses import dataclass, field
from statistics import fmean
from typing import Any, Literal

from roracle.eval.stats import BOOTSTRAP_N, BOOTSTRAP_SEED, paired_bootstrap
from roracle.eval.store import PairView, RunView

MIN_QUERIES = 200
MIN_SCOPE_QUERIES = (
    50  # ledger M3: a single kind needs >= 50 queries; the whole reference set still >= MIN_QUERIES
)
DOC_RECALL10_GAIN = 0.05  # `global` scope: doc-level d_recall@10 replaces the passage criterion
FACT_NDCG_MAX_LOSS = 0.005  # `global` scope: the fact scope must not regress
MIN_PRINCIPALS = 3  # distinct, at least one non-admin
NDCG_GAIN = 0.02
RECALL10_GAIN = 0.02
RECALL100_MAX_LOSS = 0.005
JUDGE_MAX_LOSS = 0.02
JUDGE_KEYS = ("faithfulness", "citation_support")
__all__ = ["BOOTSTRAP_N", "BOOTSTRAP_SEED", "Verdict", "metric_deltas", "verdict"]

Scope = Literal["all", "fact", "multihop", "global"]


@dataclass
class Verdict:
    status: Literal["PASS", "FAIL", "INSUFFICIENT"]
    reasons: list[str] = field(default_factory=list)
    deltas: dict[str, dict[str, Any]] = field(default_factory=dict)  # metric -> {a, b, delta, lo, hi, n}


def _value(pair: PairView, key: str) -> float | None:
    src = pair.judge if key in JUDGE_KEYS else pair.scores
    v = (src or {}).get(key)
    return None if v is None else float(v)


def _by_key(run: RunView, scope: str) -> dict[tuple[str, str], PairView]:
    return {(p.query_key, p.principal): p for p in run.pairs if scope == "all" or p.kind == scope}


def _per_query(pairs: list[tuple[str, float, float]]) -> tuple[list[float], list[float]]:
    by_q: dict[str, list[tuple[float, float]]] = {}
    for q, a, b in pairs:
        by_q.setdefault(q, []).append((a, b))
    qs = sorted(by_q)
    return [fmean(x for x, _ in by_q[q]) for q in qs], [fmean(y for _, y in by_q[q]) for q in qs]


def metric_deltas(base: RunView, cand: RunView, scope: Scope = "all") -> dict[str, dict[str, Any]]:
    """Every shared metric: per-query means A/B, delta and its paired-bootstrap 95% interval."""
    b_pairs, c_pairs = _by_key(base, scope), _by_key(cand, scope)
    shared = sorted(b_pairs.keys() & c_pairs.keys())
    keys: list[str] = []
    for pv in (b_pairs[k] for k in shared):
        keys += [k for k in (pv.scores or {}) if k not in keys]
    keys += [k for k in JUDGE_KEYS if any(_value(b_pairs[k2], k) is not None for k2 in shared)]
    out: dict[str, dict[str, Any]] = {}
    for key in keys:
        rows = []
        for k in shared:
            a, b = _value(b_pairs[k], key), _value(c_pairs[k], key)
            if a is not None and b is not None:
                rows.append((k[0], a, b))
        if not rows:
            continue
        a, b = _per_query(rows)
        delta, lo, hi = paired_bootstrap(a, b)
        out[key] = {"a": fmean(a), "b": fmean(b), "delta": delta, "lo": lo, "hi": hi, "n": len(a)}
    return out


def _principals(run: RunView) -> dict[str, bool]:
    cfg = run.config.get("principals") or {}
    refs = {p.principal.removesuffix(":pat") for p in run.pairs}
    return {r: bool(cfg.get(r, cfg.get(r + ":pat", False))) for r in refs}


def verdict(
    base: RunView, cand: RunView, *, scope: Scope = "all", latency_budget_ms: int | None = None
) -> Verdict:
    deltas = metric_deltas(base, cand, scope)
    fails: list[str] = []
    short: list[str] = []
    cm = cand.metrics
    for key in ("acl_violations", "es_acl_violations"):
        if cm.get(key):
            fails.append(f"{key} = {cm[key]} (must be 0)")

    if not (base.config.get("is_reference") and cand.config.get("is_reference")):
        short.append("not the reference set (the gate only judges the reference set)")
    if base.set_name != cand.set_name or base.path != cand.path:
        short.append(
            f"runs differ in set or path ({base.set_name}/{base.path} vs {cand.set_name}/{cand.path})"
        )
    in_scope = lambda r, sc=scope: {p.query_key for p in r.pairs if sc == "all" or p.kind == sc}
    queries = in_scope(cand) & in_scope(base)
    whole = in_scope(cand, "all") & in_scope(base, "all")
    if len(whole) < MIN_QUERIES:
        short.append(f"{len(whole)} all queries in both runs (need {MIN_QUERIES})")
    if scope != "all" and len(queries) < MIN_SCOPE_QUERIES:
        short.append(f"{len(queries)} {scope} queries in both runs (need {MIN_SCOPE_QUERIES})")
    pr = _principals(cand)
    if len(pr) < MIN_PRINCIPALS or all(pr.values()):
        short.append(
            f"{len(pr)} principals, non-admin: {sum(not a for a in pr.values())} (need {MIN_PRINCIPALS}, >= 1 non-admin)"
        )
    if scope != "all" and not deltas:
        short.append(f"no {scope} queries in both runs")

    def d(key: str) -> dict[str, Any] | None:
        return deltas.get(key)

    crit = (
        (("d_recall@10", DOC_RECALL10_GAIN),)
        if scope == "global"
        else (("p_ndcg@10", NDCG_GAIN), ("p_recall@10", RECALL10_GAIN))
    )
    gains = [(key, thr) for key, thr in crit if (m := d(key)) and m["delta"] >= thr and m["lo"] > 0]
    if scope == "global":  # the router must not cost fact questions anything
        fact = metric_deltas(base, cand, "fact").get("p_ndcg@10")
        if fact is None:
            short.append("no fact queries in both runs to guard p_ndcg@10")
        elif fact["delta"] < -FACT_NDCG_MAX_LOSS:
            fails.append(f"fact p_ndcg@10 {fact['delta']:+.4f} (loss > {FACT_NDCG_MAX_LOSS})")
    # global scope (ledger I7): the candidate list is capped at 40 documents, so the passage recall@100 guard cannot
    # apply; doc-level recall@40 takes its place with the same tolerance
    guard = "d_recall@40" if scope == "global" else "p_recall@100"
    if (m := d(guard)) is None:
        short.append(f"{guard} missing")
    elif m["delta"] < -RECALL100_MAX_LOSS:
        fails.append(f"{guard} {m['delta']:+.4f} (loss > {RECALL100_MAX_LOSS})")
    for key in (
        JUDGE_KEYS if base.path == cand.path == "chat" else ()
    ):  # the judge only exists on the chat path
        if (m := d(key)) is None:
            short.append(f"{key} missing: run both sides with --judge")
        elif m["delta"] < -JUDGE_MAX_LOSS:
            fails.append(f"{key} {m['delta']:+.4f} (loss > {JUDGE_MAX_LOSS})")
    p95 = cm.get("latency_p95_ms", 0)
    if latency_budget_ms is not None and p95 > latency_budget_ms:
        fails.append(f"latency p95 {p95} ms over budget {latency_budget_ms} ms")

    if not gains:  # a missing gain is only a FAIL when the set was big enough to show one
        need = " or ".join(f"{k} >= +{t}" for k, t in crit)
        msg = f"no gain: need {need} with CI lower bound > 0"
        (short if short else fails).append(msg)
    if fails:
        return Verdict("FAIL", fails + short, deltas)
    if short:
        return Verdict("INSUFFICIENT", short, deltas)
    return Verdict(
        "PASS",
        [f"{k}: {deltas[k]['delta']:+.4f} (CI lower bound {deltas[k]['lo']:+.4f})" for k, _ in gains],
        deltas,
    )
