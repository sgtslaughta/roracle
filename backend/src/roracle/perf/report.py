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
