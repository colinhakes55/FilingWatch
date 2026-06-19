"""Token-bucket rate limiter. Thread-safe, works with httpx sync client."""

import threading
import time


class RateLimiter:
    """Allows at most `rate` calls per second using a token-bucket algorithm."""

    def __init__(self, rate: float):
        self._rate = rate          # tokens added per second
        self._tokens = rate        # start full
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait = (1.0 - self._tokens) / self._rate

        time.sleep(wait)

        with self._lock:
            self._tokens = max(0.0, self._tokens - 1.0 + wait * self._rate)
