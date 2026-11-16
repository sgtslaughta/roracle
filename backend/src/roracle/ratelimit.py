"""In-process token bucket per API replica (R1): key `pat:<id>` or `user:<id>`; cluster limit ~ replicas x limit."""

import math
import threading
import time

from fastapi import HTTPException

IDLE_S = 600.0
SWEEP_EVERY_S = 60.0


class TokenBucket:
    def __init__(self) -> None:
        self._b: dict[str, tuple[float, float]] = {}  # key -> (tokens, last)
        self._lock = threading.Lock()
        self._swept = 0.0

    def take(self, key: str, now: float, limit: int) -> float | None:
        """None = allowed, else seconds until a token is available. `limit` = capacity = tokens per minute."""
        if limit <= 0:
            return None
        rate = limit / 60.0
        with self._lock:
            if now - self._swept >= SWEEP_EVERY_S:
                self._swept = now
                # a bucket idle for IDLE_S is full again (or nearly): dropping it is lossless
                self._b = {k: v for k, v in self._b.items() if now - v[1] < IDLE_S}
            tokens, last = self._b.get(key, (float(limit), now))
            tokens = min(float(limit), tokens + max(0.0, now - last) * rate)
            if tokens >= 1.0:
                self._b[key] = (tokens - 1.0, now)
                return None
            self._b[key] = (tokens, now)
            return (1.0 - tokens) / rate

    def __len__(self) -> int:
        return len(self._b)


LIMITER = TokenBucket()


def principal_key(p) -> str:
    return f"pat:{p.pat_id}" if p.via == "pat" else f"user:{p.user_id}"


def check(p, limit: int, now: float | None = None) -> None:
    """Raise 429 + Retry-After when the principal's bucket is empty."""
    wait = LIMITER.take(principal_key(p), time.monotonic() if now is None else now, limit)
    if wait is not None:
        raise HTTPException(429, "rate limited", headers={"Retry-After": str(max(1, math.ceil(wait)))})
