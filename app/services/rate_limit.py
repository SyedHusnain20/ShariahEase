"""
A small in-memory sliding-window limiter, keyed by anything hashable (here: the user id).

It lives in the process, so it resets on restart and is per worker. That is fine for the free Space (one
worker) and for its purpose (stop a runaway client or script); it is not a security boundary on its own.
"""
import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, clock=time.monotonic, max_keys: int = 10000):
        if limit < 1 or window_seconds <= 0:
            raise ValueError("limit and window must be positive")
        self.limit, self.window, self.clock, self.max_keys = limit, window_seconds, clock, max_keys
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key) -> float:
        """Record one hit. Returns 0 if allowed, else the seconds to wait before trying again."""
        now = self.clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(self.window - (now - hits[0]), 0.001)
            hits.append(now)
            if len(self._hits) > self.max_keys:                       # keep memory bounded
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window]:
                    del self._hits[k]
            return 0.0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
