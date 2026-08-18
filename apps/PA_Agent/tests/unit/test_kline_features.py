from __future__ import annotations

from pa_agent.ai.kline_features import compute_kline_geometry_features
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame


def test_compute_kline_geometry_features_classifies_basic_bars() -> None:
    frame = KlineFrame(
        symbol="XAUUSD",
        timeframe="5m",
        bars=(
            KlineBar(seq=1, ts_open=1.0, open=10.0, high=15.0, low=9.0, close=14.5, volume=1, closed=True),
            KlineBar(seq=2, ts_open=0.0, open=11.0, high=13.0, low=10.0, close=12.0, volume=1, closed=True),
        ),
        indicators=IndicatorBundle(ema20=(12.0, 11.0), atr14=(3.0, 2.0)),
        snapshot_ts_local_ms=1,
    )

    features = compute_kline_geometry_features(frame)

    assert features[0].seq == 1
    assert features[0].bar_type == "outside_bull"
    assert features[0].ema_relation == "above"
    assert features[0].range_atr_ratio == 2.0
    assert features[0].overlap_prev_ratio == 0.5
    assert features[1].bar_type == "trend_bull"


def test_compute_kline_geometry_features_marks_multibar_patterns() -> None:
    frame = KlineFrame(
        symbol="XAUUSD",
        timeframe="5m",
        bars=(
            KlineBar(seq=1, ts_open=3.0, open=12.0, high=13.0, low=11.0, close=12.8, volume=1, closed=True),
            KlineBar(seq=2, ts_open=2.0, open=11.0, high=14.0, low=10.0, close=13.5, volume=1, closed=True),
            KlineBar(seq=3, ts_open=1.0, open=10.0, high=12.0, low=10.0, close=11.5, volume=1, closed=True),
            KlineBar(seq=4, ts_open=0.0, open=10.0, high=15.0, low=9.0, close=14.0, volume=1, closed=True),
        ),
        indicators=IndicatorBundle(
            ema20=(9.0, 9.0, 9.0, 9.0),
            atr14=(5.0, 5.0, 5.0, 5.0),
        ),
        snapshot_ts_local_ms=1,
    )

    features = compute_kline_geometry_features(frame)

    assert features[0].ioi_pattern is True
    assert features[0].gap_bar == "bull_gap"
    assert features[0].ema_gap_count == 3
    assert features[0].breakout_prev == "none"


def test_compute_kline_geometry_features_detects_inside_sequence_and_micro_double() -> None:
    frame = KlineFrame(
        symbol="XAUUSD",
        timeframe="5m",
        bars=(
            KlineBar(seq=1, ts_open=2.0, open=10.0, high=12.0, low=10.0, close=11.0, volume=1, closed=True),
            KlineBar(seq=2, ts_open=1.0, open=11.0, high=13.0, low=10.0, close=12.0, volume=1, closed=True),
            KlineBar(seq=3, ts_open=0.0, open=12.0, high=14.0, low=9.0, close=13.0, volume=1, closed=True),
        ),
        indicators=IndicatorBundle(ema20=(11.0, 11.0, 11.0), atr14=(1.0, 1.0, 1.0)),
        snapshot_ts_local_ms=1,
    )

    features = compute_kline_geometry_features(frame)

    assert features[0].inside_sequence == "ii"
    assert features[0].micro_double == "MDB"


def test_geometry_features_limit_keeps_prev_bar_context() -> None:
    """limit=2 must not slice bars before compute (regression for incremental 新增表)."""
    frame = KlineFrame(
        symbol="XAUUSD",
        timeframe="5m",
        bars=(
            KlineBar(seq=1, ts_open=3.0, open=11.5, high=12.0, low=11.0, close=11.8, volume=1, closed=True),
            KlineBar(seq=2, ts_open=2.0, open=11.0, high=11.8, low=10.9, close=11.2, volume=1, closed=True),
            KlineBar(seq=3, ts_open=1.0, open=12.0, high=14.0, low=11.0, close=13.0, volume=1, closed=True),
        ),
        indicators=IndicatorBundle(ema20=(11.0, 11.0, 11.0), atr14=(1.0, 1.0, 1.0)),
        snapshot_ts_local_ms=1,
    )
    full = compute_kline_geometry_features(frame)
    limited = compute_kline_geometry_features(frame, limit=2)
    assert len(limited) == 2
    assert limited[1].bar_type == full[1].bar_type
    assert limited[1].overlap_prev_ratio == full[1].overlap_prev_ratio
    assert limited[1].inside_sequence == full[1].inside_sequence


def test_flat_bar_type_when_zero_range_and_not_inside() -> None:
    frame = KlineFrame(
        symbol="X",
        timeframe="5m",
        bars=(
            KlineBar(seq=1, ts_open=1.0, open=10.0, high=10.0, low=10.0, close=10.0, volume=1, closed=True),
            KlineBar(seq=2, ts_open=0.0, open=9.0, high=9.0, low=8.0, close=8.5, volume=1, closed=True),
        ),
        indicators=IndicatorBundle(ema20=(10.0, 10.0), atr14=(1.0, 1.0)),
        snapshot_ts_local_ms=1,
    )
    assert compute_kline_geometry_features(frame)[0].bar_type == "flat"


def test_follow_through_1_2_uses_direction_not_extreme() -> None:
    """Follow-through should treat收盘反向为失败，而不是必须穿越极值."""
    # 多头信号棒：open=10, high=12, low=9, close=11
    # 后一根K线收盘跌破开盘价但未跌破低点 → 视为反向失败
    frame_bull = KlineFrame(
        symbol="X",
        timeframe="5m",
        bars=(
            KlineBar(seq=1, ts_open=2.0, open=8.0, high=9.0, low=7.5, close=7.8, volume=1, closed=True),
            KlineBar(seq=2, ts_open=1.0, open=10.0, high=12.0, low=9.0, close=11.0, volume=1, closed=True),
        ),
        indicators=IndicatorBundle(ema20=(8.5, 11.0), atr14=(1.0, 1.0)),
        snapshot_ts_local_ms=1,
    )
    feats_bull = compute_kline_geometry_features(frame_bull)
    assert feats_bull[1].follow_through_1_2 == "failed"

    # 空头信号棒：open=10, high=11, low=8, close=9
    # 后一根K线收盘高于开盘价但未突破高点 → 视为反向失败
    frame_bear = KlineFrame(
        symbol="X",
        timeframe="5m",
        bars=(
            KlineBar(seq=1, ts_open=2.0, open=11.5, high=12.0, low=10.5, close=11.2, volume=1, closed=True),
            KlineBar(seq=2, ts_open=1.0, open=10.0, high=11.0, low=8.0, close=9.0, volume=1, closed=True),
        ),
        indicators=IndicatorBundle(ema20=(11.2, 9.5), atr14=(1.0, 1.0)),
        snapshot_ts_local_ms=1,
    )
    feats_bear = compute_kline_geometry_features(frame_bear)
    assert feats_bear[1].follow_through_1_2 == "failed"
