from __future__ import annotations

from pa_agent.ai.kline_features import _is_inside, compute_kline_geometry_features
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame, normalize_kline_bar
from pa_agent.data.snapshot import _rebase_closed_bars


def test_normalize_kline_bar_converts_ts_open_seconds_to_ms() -> None:
    raw = KlineBar(
        seq=1, ts_open=1_718_454_600.0, open=10.0, high=12.0, low=10.0, close=11.0,
        volume=1.0, closed=True,
    )
    fixed = normalize_kline_bar(raw)
    assert fixed.ts_open == 1_718_454_600_000.0


def test_normalize_kline_bar_swaps_inverted_high_low() -> None:
    raw = KlineBar(
        seq=1, ts_open=0.0, open=10.0, high=8.0, low=16.0, close=15.0, volume=1.0, closed=True
    )
    fixed = normalize_kline_bar(raw)
    assert fixed.high == 16.0
    assert fixed.low == 8.0
    assert fixed.close == 15.0


def test_normalize_kline_bar_clamps_close_outside_range() -> None:
    raw = KlineBar(
        seq=1, ts_open=0.0, open=10.0, high=12.0, low=10.0, close=5.0, volume=1.0, closed=True
    )
    fixed = normalize_kline_bar(raw)
    assert fixed.close == 10.0


def test_rebase_closed_bars_applies_normalization() -> None:
    raw = [
        KlineBar(
            seq=99, ts_open=0.0, open=10.0, high=8.0, low=16.0, close=15.0, volume=1.0, closed=True
        )
    ]
    rebased = _rebase_closed_bars(raw)
    assert rebased[0].seq == 1
    assert rebased[0].high == 16.0
    assert rebased[0].low == 8.0


def test_inside_not_true_after_normalize_on_inverted_ohlc() -> None:
    bar = normalize_kline_bar(
        KlineBar(
            seq=1, ts_open=0.0, open=10.0, high=8.0, low=16.0, close=15.0, volume=1.0, closed=True
        )
    )
    prev = KlineBar(
        seq=2, ts_open=0.0, open=10.0, high=20.0, low=10.0, close=15.0, volume=1.0, closed=True
    )
    assert _is_inside(bar, prev) is False


def test_close_position_clamped_in_features() -> None:
    """Defense-in-depth: clamp even if a caller skips normalize_kline_bar."""
    frame = KlineFrame(
        symbol="X",
        timeframe="5m",
        bars=(
            KlineBar(
                seq=1, ts_open=1.0, open=10.0, high=12.0, low=10.0, close=20.0, volume=1, closed=True
            ),
            KlineBar(
                seq=2, ts_open=0.0, open=10.0, high=12.0, low=10.0, close=11.0, volume=1, closed=True
            ),
        ),
        indicators=IndicatorBundle(ema20=(11.0, 11.0), atr14=(2.0, 2.0)),
        snapshot_ts_local_ms=1,
    )
    feat = compute_kline_geometry_features(frame)[0]
    assert feat.close_position is not None
    assert 0.0 <= feat.close_position <= 1.0
