"""Compare reports: Markdown for `docs/eval/`, JSON for machines. No document text, ids and numbers only."""

from typing import Any

from roracle.eval.gate import Verdict
from roracle.eval.store import RunView

SHOWN = (
    "p_ndcg@10",
    "p_recall@10",
    "p_recall@100",
    "p_mrr",
    "d_ndcg@10",
    "d_recall@10",
    "faithfulness",
    "citation_support",
)


def _cfg_diff(base: RunView, cand: RunView) -> list[str]:
    a = {
        **base.config.get("overrides", {}),
        "embed_model": base.config.get("embed_model"),
        "embed_index": base.config.get("embed_index"),
    }
    b = {
        **cand.config.get("overrides", {}),
        "embed_model": cand.config.get("embed_model"),
        "embed_index": cand.config.get("embed_index"),
    }
    rows = [
        f"| {k} | {a.get(k, '-')} | {b.get(k, '-')} |"
        for k in sorted(a.keys() | b.keys())
        if a.get(k) != b.get(k)
    ]
    return ["| setting | baseline | candidate |", "|---|---|---|", *(rows or ["| (none) | - | - |"])]


def _row(name: str, m: dict[str, Any]) -> str:
    return f"| {name} | {m['a']:.4f} | {m['b']:.4f} | {m['delta']:+.4f} | [{m['lo']:+.4f}, {m['hi']:+.4f}] | {m['n']} |"


def _kinds(base: RunView, cand: RunView) -> list[str]:
    out = [
        "| kind | pairs A | pairs B | p_ndcg@10 A | p_ndcg@10 B | p_recall@10 A | p_recall@10 B |",
        "|---|---|---|---|---|---|---|",
    ]
    for kind in sorted(base.metrics["by_kind"].keys() | cand.metrics["by_kind"].keys()):
        a, b = base.metrics["by_kind"].get(kind, {}), cand.metrics["by_kind"].get(kind, {})
        f = lambda m, k: f"{m[k]:.4f}" if k in m else "-"
        out.append(
            f"| {kind} | {a.get('pairs', 0)} | {b.get('pairs', 0)} | {f(a, 'p_ndcg@10')} | {f(b, 'p_ndcg@10')} | {f(a, 'p_recall@10')} | {f(b, 'p_recall@10')} |"
        )
    return out


def markdown(base: RunView, cand: RunView, verdict: Verdict) -> str:
    bm, cm = base.metrics, cand.metrics
    lines = [
        f"# Eval compare: {cand.set_name or base.set_name} ({cand.path} path)",
        "",
        f"**Verdict: {verdict.status}**",
        "",
        *(f"- {r}" for r in verdict.reasons),
        "",
        f"Baseline run `{base.id}` {base.label!r}; candidate run `{cand.id}` {cand.label!r}.",
        "",
        "## Config diff",
        "",
        *_cfg_diff(base, cand),
        "",
        "## Metrics (per-query means over shared pairs; 95% paired-bootstrap CI)",
        "",
        "| metric | A | B | Δ | CI | queries |",
        "|---|---|---|---|---|---|",
        *(_row(k, verdict.deltas[k]) for k in SHOWN if k in verdict.deltas),
        "",
        "## By kind",
        "",
        *_kinds(base, cand),
        "",
        "## ACL and latency",
        "",
        "| | A | B |",
        "|---|---|---|",
        f"| acl_violations | {bm['acl_violations']} | {cm['acl_violations']} |",
        f"| es_acl_violations | {bm['es_acl_violations']} | {cm['es_acl_violations']} |",
        f"| empty pairs | {bm['empty_pairs']} | {cm['empty_pairs']} |",
        f"| latency p50 / p95 ms | {bm['latency_p50_ms']} / {bm['latency_p95_ms']} | {cm['latency_p50_ms']} / {cm['latency_p95_ms']} |",
        "",
    ]
    return "\n".join(lines)


def json(run: RunView) -> dict[str, Any]:
    return {
        "id": run.id,
        "set": run.set_name,
        "path": run.path,
        "label": run.label,
        "config": run.config,
        "metrics": run.metrics,
        "pairs": len(run.pairs),
    }
