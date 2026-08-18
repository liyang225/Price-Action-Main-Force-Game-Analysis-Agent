"""Sliding-window rate limiting with an injectable monotonic clock."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from threading import Lock
import time

from .protocol import DataSourceError


class RateLimitExceeded(DataSourceError):
    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(0.0, retry_after)
        super().__init__(f"rate limit exceeded; retry after {self.retry_after:.6g} seconds")


class FakeClock:
    """A manually advanced monotonic clock for offline tests."""

    def __init__(self, initial: float = 0.0) -> None:
        self._time = float(initial)

    def now(self) -> float:
        return self._time

    def __call__(self) -> float:
        return self.now()

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        self._time += float(seconds)


class RateLimiter:
    """A non-blocking, thread-safe sliding-window limiter."""

    def __init__(
        self,
        max_calls: int = 10,
        window_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_calls = max_calls
        self.window_seconds = float(window_seconds)
        self._clock = clock or time.monotonic
        self._calls: deque[float] = deque()
        self._lock = Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            now = self._clock()
            self._discard_expired(now)
            if len(self._calls) >= self.max_calls:
                return False
            self._calls.append(now)
            return True

    def require(self) -> None:
        if not self.try_acquire():
            raise RateLimitExceeded(self.retry_after())

    def retry_after(self) -> float:
        with self._lock:
            now = self._clock()
            self._discard_expired(now)
            if len(self._calls) < self.max_calls:
                return 0.0
            return max(0.0, self.window_seconds - (now - self._calls[0]))

    def _discard_expired(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()
