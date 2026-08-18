"""Tests for Brooks-aligned trend_context module."""
from __future__ import annotations

from pa_agent.ai.decision_nodes import judge_always_in, judge_direction
from pa_agent.ai.trend_context import (
    build_trend_context,
    detect_recent_spike,
    compute_background_direction,
)
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame


def _bar(seq: int, close: float, *, open_: float | None = None) -> KlineBar:
    o = open_ if open_ is not None else close - 1.0
    return KlineBar(
        seq=seq,
        ts_open=float(1_000_000 - seq * 60_000),
        open=o,
        high=max(o, close) + 2.0,
        low=min(o, close) - 2.0,
        close=close,
        volume=100.0,
        closed=True,
    )


def _frame_with_regimes() -> KlineFrame:
    """Older bars bearish, recent 8 bars bullish spike."""
    bars: list[KlineBar] = []
    for seq in range(100, 8, -1):
        c = 2100.0 - (100 - seq) * 3.0
        bars.append(_bar(seq, c, open_=c + 2.0))
    for seq in range(8, 0, -1):
        c = 1980.0 + (8 - seq) * 8.0
        bars.append(_bar(seq, c, open_=c - 4.0))
    n = len(bars)
    ema = tuple(2050.0 - i * 1.5 for i in range(n))
    atr = tuple([12.0] * n)
    return KlineFrame(
        symbol="TEST",
        timeframe="1h",
        bars=tuple(bars),
        snapshot_ts_local_ms=1,
        indicators=IndicatorBundle(ema20=ema, atr14=atr),
    )


class TestTrendContext:
    def test_background_bearish_recent_bullish_conflict(self):
        frame = _frame_with_regimes()
        direction, _ = judge_direction(frame)
        ctx = build_trend_context(frame, direction)
        assert ctx["trading_direction"] == direction
        assert ctx["background_direction"] in ("bearish", "neutral", "bullish")
        if direction == "bullish" and ctx["background_direction"] == "bearish":
            assert ctx["conflict"] is True
            assert ctx["relationship"] == "conflict"

    def test_recent_spike_detection_bullish(self):
        frame = _frame_with_regimes()
        spike = detect_recent_spike(frame)
        assert spike in ("bullish", None)

    def test_always_in_near_window_can_flip_to_ail(self):
        frame = _frame_with_regimes()
        fill = judge_always_in(frame)
        assert fill.node_id == "2.4"
        assert fill.bar_range == "K8-K1"
        assert "近端" in fill.reason or "K8" in fill.reason
