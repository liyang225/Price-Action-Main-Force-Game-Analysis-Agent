"""Tests for forming-bar close detection."""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from pa_agent.data.bar_close_wait import (
    current_forming_ts,
    forming_bar_has_closed,
    has_forming_bar_at_head,
    is_bar_still_forming,
    next_ashare_session_open_ms,
    seconds_until_bar_closes,
    timeframe_to_seconds,
)
from pa_agent.data.base import KlineBar


def _bar(ts: int) -> KlineBar:
    return KlineBar(
        seq=1,
        ts_open=float(ts),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
        closed=False,
    )


def test_timeframe_to_seconds() -> None:
    assert timeframe_to_seconds("5m") == 300
    assert timeframe_to_seconds("1h") == 3600
    assert timeframe_to_seconds("2h") == 7200


def test_seconds_until_bar_closes() -> None:
    ts_open = 1_000_000
    now = ts_open + 240_000  # 4 min into 5m bar
    assert seconds_until_bar_closes(ts_open, "5m", now_ms=now) == 60
    assert seconds_until_bar_closes(ts_open, "5m", now_ms=ts_open + 300_000) == 0


def test_seconds_until_bar_closes_offset_multiple_durations() -> None:
    """If ts_open has a constant offset by whole durations, countdown must not drift."""
    now_ms = 10_000_000
    ts_open_ms = now_ms + 8 * 3600 * 1000  # offset by 8 hours
    assert seconds_until_bar_closes(ts_open_ms, "1m", now_ms=now_ms) == 60


def test_forming_bar_has_closed_when_ts_changes() -> None:
    now_ms = int(time.time() * 1000)
    waited = now_ms - 120_000
    before = [_bar(waited), _bar(waited - 300_000)]
    after = [_bar(now_ms), _bar(waited)]
    assert current_forming_ts(before, "5m", now_ms=now_ms - 60_000) == waited
    assert not forming_bar_has_closed(waited, before, "5m", now_ms=now_ms - 60_000)
    assert forming_bar_has_closed(waited, after, "5m", now_ms=now_ms)


def test_stale_unclosed_flag_after_bar_period_not_forming() -> None:
    """TradingView-style closed=False but bar period ended → treat as closed."""
    ts_open = 1_700_000_000_000
    now_ms = ts_open + 20 * 60 * 1000  # 20m after open on 15m bar
    head = _bar(ts_open)
    assert not is_bar_still_forming(head, "15m", now_ms=now_ms)
    assert not has_forming_bar_at_head([head], "15m", now_ms=now_ms)
    assert current_forming_ts([head], "15m", now_ms=now_ms) is None
    assert forming_bar_has_closed(ts_open, [head], "15m", now_ms=now_ms)


def test_forming_closed_with_server_now_when_local_lags() -> None:
    """MT5 clock skew: server time must drive forming detection, not local lag."""
    offset_ms = 3 * 3600 * 1000
    ts_open = 1_700_000_000_000
    duration_ms = 5 * 60 * 1000
    server_now = ts_open + duration_ms + 1000
    local_now = server_now - offset_ms
    head = _bar(ts_open)
    assert is_bar_still_forming(head, "5m", now_ms=local_now)
    assert not is_bar_still_forming(head, "5m", now_ms=server_now)
    assert has_forming_bar_at_head([head], "5m", now_ms=local_now)
    assert not has_forming_bar_at_head([head], "5m", now_ms=server_now)


def test_reference_now_ms_uses_server_time_ms() -> None:
    import time
    from pa_agent.data.bar_close_wait import reference_now_ms

    # Simulate a fresh broker tick: server time is very close to local time
    # (within 60 s), so reference_now_ms should return the broker server time.
    fresh_server_ms = int(time.time() * 1000) - 5_000  # 5 s behind local → within threshold

    class _Src:
        def server_time_ms(self) -> int:
            return fresh_server_ms

    assert reference_now_ms(data_source=_Src()) == fresh_server_ms


def test_active_intraday_bar_still_forming() -> None:
    ts_open = 1_700_000_000_000
    now_ms = ts_open + 5 * 60 * 1000
    head = _bar(ts_open)
    assert is_bar_still_forming(head, "15m", now_ms=now_ms)
    assert has_forming_bar_at_head([head], "15m", now_ms=now_ms)


def test_ashare_lunch_waits_for_the_first_afternoon_bar_to_close() -> None:
    cn_tz = ZoneInfo("Asia/Shanghai")
    noon_ms = int(datetime(2026, 7, 30, 12, 0, tzinfo=cn_tz).timestamp() * 1000)
    afternoon_open_ms = int(
        datetime(2026, 7, 30, 13, 0, tzinfo=cn_tz).timestamp() * 1000
    )
    afternoon_close_ms = afternoon_open_ms + 15 * 60_000

    assert next_ashare_session_open_ms("15m", symbol="SH.600519", now_ms=noon_ms) == (
        afternoon_open_ms
    )
    assert seconds_until_bar_closes(
        afternoon_close_ms,
        "15m",
        symbol="SH.600519",
        now_ms=noon_ms,
        timestamp_is_close=True,
    ) == 75 * 60
    assert not forming_bar_has_closed(
        afternoon_close_ms,
        [],
        "15m",
        symbol="SH.600519",
        now_ms=noon_ms,
        timestamp_is_close=True,
    )
    assert forming_bar_has_closed(
        afternoon_close_ms,
        [],
        "15m",
        symbol="SH.600519",
        now_ms=afternoon_close_ms,
        timestamp_is_close=True,
    )


def test_close_labeled_bar_stays_forming_only_until_its_label_time() -> None:
    close_label_ms = 1_700_000_000_000
    bar = KlineBar(
        seq=1,
        ts_open=float(close_label_ms),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
        closed=False,
        timestamp_is_close=True,
    )

    assert is_bar_still_forming(bar, "15m", now_ms=close_label_ms - 1)
    assert not is_bar_still_forming(bar, "15m", now_ms=close_label_ms)
