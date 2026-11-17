"""Paired bootstrap for the gain gate."""

import numpy as np

BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 0


def paired_bootstrap(
    a: list[float], b: list[float], n: int = BOOTSTRAP_N, seed: int = BOOTSTRAP_SEED
) -> tuple[float, float, float]:
    """(delta, lo, hi): mean of `b - a` (b = candidate) and its 95% percentile interval over `n` resamples
    of the pairs. Deterministic for a given seed."""
    if len(a) != len(b):
        raise ValueError("paired samples must have equal length")
    if not a:
        return 0.0, 0.0, 0.0
    d = np.asarray(b, dtype=float) - np.asarray(a, dtype=float)
    idx = np.random.default_rng(seed).integers(0, len(d), size=(n, len(d)))
    lo, hi = np.percentile(d[idx].mean(axis=1), [2.5, 97.5])
    return float(d.mean()), float(lo), float(hi)
