"""Admission for global answers: a process-wide cap on in-flight map calls and on admitted questions, and one
global question per user. Nothing queues: when busy the turn takes the fact path."""

import threading
import time


class Slots:
    def __init__(self) -> None:
        self.cv = threading.Condition()
        self.used = 0
        self.users: dict[str, float] = {}  # user -> expiry (a lost release cannot lock a user out forever)

    def admitted(self) -> int:
        now = time.monotonic()
        return sum(exp > now for exp in self.users.values())

    def admit(self, cap: int, user: str, ttl: float, max_admitted: int = 1 << 30) -> bool:
        """One question per user; at most `max_admitted` questions admitted process-wide (each may queue up to
        `global_concurrency` calls, so unbounded admission would only make everyone wait for slots); no admission
        while every call slot is taken."""
        with self.cv:
            now = time.monotonic()
            if self.users.get(user, 0) > now or self.used >= cap or self.admitted() >= max_admitted:
                return False
            self.users[user] = now + ttl
            return True

    def leave(self, user: str) -> None:
        with self.cv:
            self.users.pop(user, None)

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
