from __future__ import annotations

from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.records.analysis_history import (
    compute_incremental_bar_delta,
    count_new_bars_since_record,
)
from pa_agent.records.schema import AnalysisRecord, RecordMeta


def _record_with_latest(ts_open: float) -> AnalysisRecord:
    return AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso="2026-01-01T00:00:00.000",
            timestamp_local_ms=1,
            symbol="XAUUSD",
            timeframe="1h",
            bar_count=3,
            ai_provider={},
        ),
        kline_data=[
            {
                "seq": 1,
                "ts_open": ts_open,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
                "closed": True,
            }
        ],
        htf_text="",
        stage1_messages=[],
        stage1_response=None,
        stage1_diagnosis={"cycle_position": "spike"},
        stage2_messages=[],
        stage2_response=None,
        stage2_decision={"decision": {"order_type": "不下单"}},
        strategy_files_used=[],
        experience_loaded=[],
        exception=None,
        usage_total={},
    )


def _frame(timestamps: list[float]) -> KlineFrame:
    bars = tuple(
        KlineBar(
            seq=i + 1,
            ts_open=ts,
            open=1,
            high=1,
            low=1,
            close=1,
            volume=1,
            closed=True,
        )
        for i, ts in enumerate(timestamps)
    )
    return KlineFrame(
        symbol="XAUUSD",
        timeframe="1h",
        bars=bars,
        indicators=IndicatorBundle(
            ema20=tuple(1.0 for _ in bars),
            atr14=tuple(1.0 for _ in bars),
        ),
        snapshot_ts_local_ms=1,
    )


def test_count_new_bars_since_record_uses_previous_latest_index():
    previous = _record_with_latest(1000.0)
    frame = _frame([3000.0, 2000.0, 1000.0, 0.0])

    assert count_new_bars_since_record(frame, previous) == 2


def test_count_new_bars_since_record_returns_none_without_overlap():
    previous = _record_with_latest(1000.0)
    frame = _frame([4000.0, 3000.0, 2000.0])

    assert count_new_bars_since_record(frame, previous) is None


def test_count_new_bars_since_record_normalizes_millisecond_timestamps():
    previous = _record_with_latest(1_700_000_000_000.0)
    frame = _frame([1_700_003_600.0, 1_700_000_000.0])

    assert count_new_bars_since_record(frame, previous) == 1


def test_compute_incremental_delta_only_counts_ts_after_anchor():
    """Anchor bar still in window must not be counted as a new bar."""
    previous = _record_with_latest(2000.0)
    frame = _frame([3000.0, 2000.0, 1000.0])

    delta = compute_incremental_bar_delta(frame, previous)
    assert delta is not None
    assert delta.new_count == 1
    assert delta.new_bar_ts_opens == (3000.0,)


def test_compute_incremental_delta_two_new_bars():
    previous = _record_with_latest(1000.0)
    frame = _frame([3000.0, 2000.0, 1000.0])

    delta = compute_incremental_bar_delta(frame, previous)
    assert delta is not None
    assert delta.new_count == 2
    assert delta.new_bar_ts_opens == (3000.0, 2000.0)
