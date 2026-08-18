from __future__ import annotations

import pytest

from src.data.rate_limiter import FakeClock, RateLimiter, RateLimitExceeded


def test_rate_limiter_allows_ten_calls_and_releases_the_boundary() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_calls=10, window_seconds=30, clock=clock.now)

    assert all(limiter.try_acquire() for _ in range(10))
    assert not limiter.try_acquire()

    clock.advance(29.999)
    assert not limiter.try_acquire()
    clock.advance(0.001)
    assert limiter.try_acquire()


def test_rate_limiter_can_report_retry_delay_without_sleeping() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_calls=1, window_seconds=30, clock=clock.now)
    assert limiter.try_acquire()

    assert limiter.retry_after() == pytest.approx(30)
    with pytest.raises(RateLimitExceeded) as exc_info:
        limiter.require()
    assert exc_info.value.retry_after == pytest.approx(30)
