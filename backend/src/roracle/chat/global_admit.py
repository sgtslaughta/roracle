"""Admission for global answers: a process-wide cap on in-flight map calls and on admitted questions, and one
global question per user. Nothing queues: when busy the turn takes the fact path."""

import threading
import time


class Slots:
    def __init__(self) -> None:
        self.cv = threading.Condition()
        self.used = 0
        self.users: dict[str, float] = {}  # user -> expiry (a lost release cannot lock a user out forever)
        self.tokens: dict[str, int] = {}  # user -> token of the admission that holds the slot
        self.seq = 0

    def admitted(self) -> int:
        now = time.monotonic()
        return sum(exp > now for exp in self.users.values())

    def admit(self, cap: int, user: str, ttl: float, max_admitted: int = 1 << 30) -> bool:
        return bool(self.enter(cap, user, ttl, max_admitted))

    def enter(self, cap: int, user: str, ttl: float, max_admitted: int = 1 << 30) -> int:
        """One question per user; at most `max_admitted` questions admitted process-wide (each may queue up to
        `global_concurrency` calls, so unbounded admission would only make everyone wait for slots); no admission
        while every call slot is taken."""
        with self.cv:
            now = time.monotonic()
            if self.users.get(user, 0) > now or self.used >= cap or self.admitted() >= max_admitted:
                return 0
            self.users[user] = now + ttl
            self.seq += 1
            self.tokens[user] = self.seq
            return self.seq  # non-zero: pass it to `leave` so a stale release never frees a newer admission

    def leave(self, user: str, token: int | None = None) -> None:
        with self.cv:
            if token is not None and self.tokens.get(user) != token:
                return  # the slot now belongs to a later admission
            self.users.pop(user, None)
            self.tokens.pop(user, None)

    def acquire(self, cap: int, timeout: float) -> bool:
        end = time.monotonic() + timeout
        with self.cv:
            while self.used >= cap:
                if (rem := end - time.monotonic()) <= 0:
                    return False
                self.cv.wait(rem)
            self.used += 1
            return True

    def release(self) -> None:
        with self.cv:
            self.used -= 1
            self.cv.notify()


SLOTS = Slots()
