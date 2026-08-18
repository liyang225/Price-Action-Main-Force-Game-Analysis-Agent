"""End-to-end: analysis frame uses warmup buffer for EMA/ATR; features keep prev context."""
from __future__ import annotations

import math

from pa_agent.ai.kline_features import compute_kline_geometry_features
from pa_agent.data.base import KlineBar
from pa_agent.data.snapshot import (
    INDICATOR_WARMUP_BARS,
    build_analysis_frame,
    compute_indicators,
)
from pa_agent.indicators.ema import ema_full


def _bars_newest_first(closes: list[float]) -> list[KlineBar]:
    n = len(closes)
    out: list[KlineBar] = []
    for i, close in enumerate(reversed(closes)):
        seq = i + 1
        out.append(
            KlineBar(
                seq=seq,
                ts_open=float(seq),
                open=close - 0.1,
                high=close + 0.2,
                low=close - 0.2,
                close=close,
                volume=1.0,
                closed=True,
            )
        )
    return out


def test_build_analysis_frame_ema_uses_warmup_buffer() -> None:
    """K1 EMA20 should match ema on full history, not on the last n bars only."""
    closes = [100.0 + (i * 0.2) + (5.0 if i >= 40 else 0.0) for i in range(80)]
    bars_raw = _bars_newest_first(closes)
    n = 25

    frame = build_analysis_frame(bars_raw, n, "XAUUSD", "5m")
    assert frame is not None
    assert len(frame.bars) == n

    ema_on_full = ema_full(closes, period=20)[-1]
    ema_k1 = frame.indicators.ema20[0]
    assert not math.isnan(ema_k1)
    assert abs(ema_k1 - ema_on_full) < 1e-9

    # Narrow window only (no warmup) would differ materially.
    narrow = compute_indicators(frame.bars)
    ema_narrow_only = ema_full(closes[-n:], period=20)[-1]
    assert abs(narrow.ema20[0] - ema_narrow_only) < 1e-9
    assert abs(ema_k1 - ema_narrow_only) > 0.01


def test_build_analysis_frame_falls_back_when_buffer_unavailable() -> None:
    """When fewer than n+warmup bars exist, still return n bars if possible."""
    closes = [50.0 + i for i in range(25)]
    bars_raw = _bars_newest_first(closes)
    frame = build_analysis_frame(bars_raw, 20, "XAUUSD", "5m")
    assert frame is not None
    assert len(frame.bars) == 20


def test_feature_limit_uses_full_frame_prev_context() -> None:
    """Incremental limit must not drop prev bar for overlap / inside."""
    frame = build_analysis_frame(
        _bars_newest_first([10.0, 10.5, 11.0, 11.2, 11.1, 11.3, 11.4]),
        5,
        "XAUUSD",
        "5m",
    )
    assert frame is not None

    full = compute_kline_geometry_features(frame)
    limited = compute_kline_geometry_features(frame, limit=2)
    assert len(limited) == 2
    assert limited[0].seq == 1
    assert limited[1].overlap_prev_ratio == full[1].overlap_prev_ratio
    assert limited[1].bar_type == full[1].bar_type


def test_warmup_constant_covers_ema_period() -> None:
    assert INDICATOR_WARMUP_BARS >= 20
