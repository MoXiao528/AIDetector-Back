from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
from time import monotonic
from typing import Deque


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def reset(self) -> None:
        with self._lock:
            self._events.clear()

    def allow(self, bucket: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = monotonic()
        cutoff = now - max(window_seconds, 1)

        with self._lock:
            events = self._events[bucket]
            while events and events[0] <= cutoff:
                events.popleft()

            if len(events) >= limit:
                retry_after = max(int(events[0] + window_seconds - now), 1)
                return False, retry_after

            events.append(now)
            return True, 0


auth_rate_limiter = InMemoryRateLimiter()
