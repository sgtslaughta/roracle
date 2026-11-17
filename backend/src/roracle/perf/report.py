"""Latency report: nearest-rank percentiles and the budget gate."""

import math
from dataclasses import dataclass


def percentile(xs: list[float], q: float) -> float:
    """Nearest-rank percentile, q in [0, 100]; 0.0 for no samples."""
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[max(1, math.ceil(q / 100 * len(s))) - 1]


@dataclass(frozen=True)
class Report:
    p50: float
    p95: float
    p99: float
    errors: int
    n: int

    @classmethod
    def of(cls, latencies_ms: list[float], errors: int = 0) -> "Report":
        return cls(
            percentile(latencies_ms, 50),
            percentile(latencies_ms, 95),
            percentile(latencies_ms, 99),
            errors,
            len(latencies_ms) + errors,
        )


def gate(report: Report, budget_ms: float, *, q: str = "p95") -> bool:
    """True when the chosen percentile is within budget and nothing errored."""
    return report.errors == 0 and getattr(report, q) <= budget_ms


def check(spec: dict, values: dict[str, float], factor: float = 1.0) -> tuple[bool, str]:
    """Gate `values` against a budget `spec`: {p95: 400} (a latency percentile, ms), {min: 1000} / {max: 0} (a single
    measurement in `values["value"]`). `factor` >= 1 loosens the budget for a smaller / noisier profile: latency and
    `max` limits x factor... except `max: 0`, which stays 0; `min` limits / factor. Any errors fail the gate."""
    parts: list[str] = []
    bad: list[str] = []
    if values.get("errors"):
        bad.append(f"{int(values['errors'])} errors")
    for k, limit in spec.items():
        v = values.get("value") if k in ("min", "max") else values.get(k)
        if v is None:
            bad.append(f"{k}: no sample")
            continue
        if k == "min":
            lim, ok = limit / factor, v >= limit / factor
        else:
            lim = limit * factor
            ok = v <= lim
        parts.append(f"{k} {v:g} {'>=' if k == 'min' else '<='} {lim:g}")
        if not ok:
            bad.append(parts[-1])
    return not bad, "; ".join(parts + [f"FAILED {b}" for b in bad])
