"""Tests for build_analysis_frame when the buffer has no forming bar."""
from __future__ import annotations

from pa_agent.data.base import KlineBar
from pa_agent.data.snapshot import build_analysis_frame, build_live_frame


def _bar(seq: int, ts_ms: float, *, close: float = 1.5) -> KlineBar:
    return KlineBar(
        seq=seq,
        ts_open=ts_ms,
        open=close - 0.5,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=100.0,
        closed=True,
    )


def test_analysis_frame_without_forming_uses_newest_closed_as_k1() -> None:
    """When index 0 is already closed, K1 must not be skipped."""
    import time

    now_ms = int(time.time() * 1000)
    bars = [
        _bar(1, float(now_ms - 60_000), close=10.0),
        _bar(2, float(now_ms - 960_000), close=9.0),
        _bar(3, float(now_ms - 1_860_000), close=8.0),
    ]
    frame = build_analysis_frame(bars, 3, "XAUUSD", "15m", now_ms=now_ms)
    assert frame is not None
    assert len(frame.bars) == 3
    assert frame.bars[0].close == 10.0
    assert frame.bars[0].seq == 1
    assert frame.bars[2].close == 8.0


def test_analysis_frame_with_forming_skips_index_zero() -> None:
    import time

    now_ms = int(time.time() * 1000)
    forming = KlineBar(
        seq=0,
        ts_open=float(now_ms - 60_000),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.2,
        volume=50.0,
        closed=False,
    )
    bars = [
        forming,
        _bar(1, float(now_ms - 900_000), close=10.0),
        _bar(2, float(now_ms - 1_800_000), close=9.0),
    ]
    frame = build_analysis_frame(bars, 2, "XAUUSD", "15m", now_ms=now_ms)
    assert frame is not None
    assert len(frame.bars) == 2
    assert frame.bars[0].close == 10.0


def test_live_frame_without_forming_does_not_mark_k1_as_forming() -> None:
    import time

    now_ms = int(time.time() * 1000)
    bars = [_bar(1, float(now_ms - 60_000)), _bar(2, float(now_ms - 960_000))]
    frame = build_live_frame(bars, 2, "XAUUSD", "15m", now_ms=now_ms)
    assert frame is not None
    assert all(b.closed for b in frame.bars)


def test_analysis_keeps_head_when_closed_false_but_period_ended() -> None:
    """Stale closed=False after halt must not skip the newest bar."""
    ts_open = 1_700_000_000_000.0
    now_ms = int(ts_open) + 30 * 60 * 1000
    stale = KlineBar(
        seq=1,
        ts_open=ts_open,
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.5,
        volume=1.0,
        closed=False,
    )
    bars = [
        stale,
        _bar(2, float(now_ms - 960_000), close=9.0),
        _bar(3, float(now_ms - 1_860_000), close=8.0),
    ]
    from pa_agent.data.bar_close_wait import has_forming_bar_at_head

    assert not has_forming_bar_at_head(bars, "15m", now_ms=now_ms)
    frame = build_analysis_frame(bars, 3, "688981", "15m", now_ms=now_ms)
    assert frame is not None
    assert frame.bars[0].close == 10.5
    assert frame.bars[0].ts_open == ts_open
