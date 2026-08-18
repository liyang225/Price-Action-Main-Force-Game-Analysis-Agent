from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import Qt

from pa_agent.records.history_analysis import (
    count_history_records,
    list_history_entries,
    save_history_record,
    stock_history_dir,
)
from pa_agent.records.schema import AnalysisRecord, RecordMeta
from pa_agent.records.trade_rules import (
    ManualEntryOverride,
    SETTLEMENT_T0,
    SETTLEMENT_T1,
)


def _record(
    symbol: str = "NASDAQ:AAPL",
    timeframe: str = "15m",
    *,
    timestamp_ms: int = 1_785_000_672_000,
    close: float = 10.5,
    order_type: str | None = "buy",
    order_direction: str | None = None,
    entry: float | None = None,
    tp: float | None = None,
    sl: float | None = None,
    bars: list[dict] | None = None,
    direction: str = "bullish",
    cycle_position: str = "broad_channel",
    resistance_levels: list[str] | None = None,
    support_levels: list[str] | None = None,
    timestamp_iso: str | None = None,
) -> AnalysisRecord:
    decision = None
    if order_type is not None:
        decision = {
            "decision": {
                "order_type": order_type,
                "order_direction": order_direction,
                "entry_price": entry,
                "take_profit_price": tp,
                "stop_loss_price": sl,
            },
            "diagnosis_summary": {
                "direction": direction,
                "cycle_position": cycle_position,
            },
        }
    return AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso=timestamp_iso or f"2026-07-24T10:11:12.{timestamp_ms}+08:00",
            timestamp_local_ms=timestamp_ms,
            symbol=symbol,
            timeframe=timeframe,
            bar_count=100,
            ai_provider={"model": "test", "api_key": "****"},
        ),
        kline_data=bars or [
            {
                "seq": 1,
                "ts_open": 1_785_000_000,
                "open": 10,
                "high": 11,
                "low": 9,
                "close": close,
                "volume": 100,
                "closed": True,
            }
        ],
        htf_text="",
        stage1_messages=[],
        stage1_response=None,
        stage1_diagnosis={
            "direction": direction,
            "cycle_position": cycle_position,
            "resistance_levels": resistance_levels or [],
            "support_levels": support_levels or [],
        },
        stage2_messages=[],
        stage2_response=None,
        stage2_decision=decision,
        strategy_files_used=[],
        experience_loaded=[],
        exception=None,
        usage_total={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )


def test_save_history_record_uses_per_symbol_folder(tmp_path):
    root = tmp_path / "history"
    record = _record()

    path = save_history_record(record, root=root)

    assert path is not None
    assert path.parent == stock_history_dir("NASDAQ:AAPL", root)
    assert path.exists()
    assert count_history_records("NASDAQ:AAPL", root=root) == 1


def test_history_entries_filter_by_timeframe_and_skip_incomplete(tmp_path):
    root = tmp_path / "history"
    save_history_record(_record(timeframe="15m"), root=root)
    save_history_record(_record(timeframe="1h"), root=root)
    assert save_history_record(_record(timeframe="1d", order_type=None), root=root) is None

    entries = list_history_entries(symbol="NASDAQ:AAPL", timeframe="15m", root=root)

    assert len(entries) == 1
    assert entries[0].timeframe == "15m"
    assert entries[0].decision_label == "做多单"


def test_history_entry_formats_trade_fields_and_price_change(tmp_path):
    root = tmp_path / "history"
    save_history_record(
        _record(
            timestamp_ms=1_000,
            close=10.0,
            order_type="市价单",
            order_direction="做多",
            entry=9.8,
            tp=11.0,
            sl=9.0,
            direction="bullish",
            cycle_position="broad_channel",
            resistance_levels=["11.2", "11.5"],
            support_levels=["9.5"],
        ),
        root=root,
    )
    save_history_record(
        _record(
            timestamp_ms=2_000,
            close=10.5,
            order_type="不下单",
            order_direction="做多",
            direction="bearish",
        ),
        root=root,
    )

    newest, oldest = list_history_entries(symbol="NASDAQ:AAPL", timeframe="15m", root=root)

    assert newest.price_change == 0.5
    assert newest.price_change_pct == 5.0
    assert newest.decision_label == "不下单"
    assert newest.entry_price is None
    assert newest.take_profit_price is None
    assert newest.stop_loss_price is None
    assert newest.trend_label == "下跌"
    assert oldest.decision_label == "做多单"
    assert oldest.entry_price == 9.8
    assert oldest.cycle_label == "上涨宽通道"
    assert oldest.resistance_levels == ("11.2",)
    assert oldest.support_levels == ("9.5",)


def test_history_entry_uses_chart_levels_and_displayed_current_trend(tmp_path):
    root = tmp_path / "history"
    save_history_record(
        _record(
            direction="bearish",
            cycle_position="trading_range",
            resistance_levels=["11.2", "11.5"],
            support_levels=["9.7-9.8", "9.4"],
        ),
        root=root,
    )

    entry = list_history_entries(symbol="NASDAQ:AAPL", root=root)[0]

    assert entry.trend_label == "震荡偏空"
    assert entry.cycle_label == "下跌交易区间"
    assert entry.resistance_levels == ("11.2",)
    assert entry.support_levels == ("9.7-9.8",)


def test_t0_trade_event_is_inserted_above_its_source_record(tmp_path):
    root = tmp_path / "history"
    old_bar = {
        "seq": 1, "ts_open": 1_000, "open": 10, "high": 10.2,
        "low": 9.8, "close": 10, "volume": 100, "closed": True,
    }
    save_history_record(
        _record(
            timestamp_ms=1_000,
            order_type="限价单",
            order_direction="做多",
            entry=10,
            tp=11,
            sl=9,
            bars=[old_bar],
        ),
        root=root,
    )
    current_bars = [
        {**old_bar, "seq": 1, "ts_open": 3_000, "high": 11.2, "low": 10.4, "close": 11.1},
        {**old_bar, "seq": 2, "ts_open": 2_000, "high": 10.2, "low": 9.9, "close": 10.0},
        old_bar,
    ]
    save_history_record(
        _record(timestamp_ms=4_000, order_type="不下单", bars=current_bars),
        root=root,
    )

    entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T0,
        root=root,
    )

    newest, event_row, source = entries
    assert newest.row_kind == "analysis"
    assert event_row.row_kind == "trade_event"
    assert event_row.trade_event is not None
    assert event_row.trade_event.outcome == "tp"
    assert event_row.trade_event.exit_price == 11.1
    assert event_row.trade_event.return_pct == 11.0
    assert source.row_kind == "analysis"
    assert event_row.path == source.path


def test_flexible_limit_entry_allows_configured_price_tolerance(tmp_path):
    root = tmp_path / "history"
    old_bar = {
        "seq": 1, "ts_open": 1_000, "open": 1.826, "high": 1.828,
        "low": 1.823, "close": 1.825, "volume": 100, "closed": True,
    }
    source_path = save_history_record(
        _record(
            timestamp_ms=1_000,
            order_type="限价单",
            order_direction="做多",
            entry=1.824,
            tp=1.833,
            sl=1.815,
            bars=[old_bar],
        ),
        root=root,
    )
    assert source_path is not None
    save_history_record(
        _record(
            timestamp_ms=4_000,
            order_type="不下单",
            bars=[
                {
                    **old_bar,
                    "seq": 1,
                    "ts_open": 3_000,
                    "open": 1.830,
                    "high": 1.833,
                    "low": 1.825,
                    "close": 1.832,
                },
                old_bar,
            ],
        ),
        root=root,
    )

    strict_entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T0,
        root=root,
    )
    flexible_entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T0,
        entry_tolerance_ticks=1,
        root=root,
    )

    assert all(entry.row_kind == "analysis" for entry in strict_entries)
    event = next(entry.trade_event for entry in flexible_entries if entry.row_kind == "trade_event")
    assert event is not None
    assert event.entry_price == 1.825
    assert event.outcome == "tp"


def test_manual_entry_override_uses_actual_time_and_price(tmp_path):
    root = tmp_path / "history"
    old_bar = {
        "seq": 1, "ts_open": 1_000, "open": 1.826, "high": 1.828,
        "low": 1.823, "close": 1.825, "volume": 100, "closed": True,
    }
    source_path = save_history_record(
        _record(
            timestamp_ms=1_000,
            order_type="限价单",
            order_direction="做多",
            entry=1.824,
            tp=1.833,
            sl=1.815,
            bars=[old_bar],
        ),
        root=root,
    )
    assert source_path is not None
    save_history_record(
        _record(
            timestamp_ms=4_000,
            order_type="不下单",
            bars=[
                {
                    **old_bar,
                    "seq": 2,
                    "ts_open": 3_000,
                    "open": 1.830,
                    "high": 1.833,
                    "low": 1.825,
                    "close": 1.832,
                },
                {
                    **old_bar,
                    "seq": 1,
                    "ts_open": 2_000,
                    "open": 1.830,
                    "high": 1.831,
                    "low": 1.829,
                    "close": 1.830,
                },
                old_bar,
            ],
        ),
        root=root,
    )

    entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T0,
        entry_overrides={
            str(source_path.resolve()): ManualEntryOverride(timestamp_ms=3_000, price=1.826)
        },
        root=root,
    )
    event = next(entry.trade_event for entry in entries if entry.row_kind == "trade_event")

    assert event is not None
    assert event.entry_timestamp_ms == 3_000
    assert event.entry_price == 1.826
    assert event.exit_price == 1.832
    assert event.outcome == "tp"


def test_t0_same_bar_dual_touch_uses_open_close_direction_for_long(tmp_path):
    root = tmp_path / "history"
    old_bar = {
        "seq": 1, "ts_open": 1_000, "open": 10, "high": 10.2,
        "low": 9.8, "close": 10, "volume": 100, "closed": True,
    }
    save_history_record(
        _record(
            timestamp_ms=1_000,
            order_type="市价单",
            order_direction="做多",
            entry=10,
            tp=11,
            sl=9,
            bars=[old_bar],
        ),
        root=root,
    )
    save_history_record(
        _record(
            timestamp_ms=4_000,
            order_type="不下单",
            bars=[
                {
                    **old_bar,
                    "seq": 1,
                    "ts_open": 3_000,
                    "open": 10,
                    "high": 11.2,
                    "low": 8.8,
                    "close": 10.5,
                },
                old_bar,
            ],
        ),
        root=root,
    )

    entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T0,
        root=root,
    )
    event = next(entry.trade_event for entry in entries if entry.row_kind == "trade_event")

    assert event is not None
    assert event.outcome == "tp"
    assert event.exit_price == 10.5
    assert event.return_pct == 5.0


def test_t0_same_bar_dual_touch_uses_open_close_direction_for_short(tmp_path):
    root = tmp_path / "history"
    old_bar = {
        "seq": 1, "ts_open": 1_000, "open": 10, "high": 10.2,
        "low": 9.8, "close": 10, "volume": 100, "closed": True,
    }
    save_history_record(
        _record(
            timestamp_ms=1_000,
            order_type="市价单",
            order_direction="做空",
            entry=10,
            tp=9,
            sl=11,
            bars=[old_bar],
        ),
        root=root,
    )
    save_history_record(
        _record(
            timestamp_ms=4_000,
            order_type="不下单",
            bars=[
                {
                    **old_bar,
                    "seq": 1,
                    "ts_open": 3_000,
                    "open": 10,
                    "high": 11.2,
                    "low": 8.8,
                    "close": 9.5,
                },
                old_bar,
            ],
        ),
        root=root,
    )

    entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T0,
        root=root,
    )
    event = next(entry.trade_event for entry in entries if entry.row_kind == "trade_event")

    assert event is not None
    assert event.outcome == "tp"
    assert event.exit_price == 9.5
    assert event.return_pct == 5.0


def test_pending_short_order_is_invalidated_when_entry_bar_hits_stop(tmp_path):
    root = tmp_path / "history"
    old_bar = {
        "seq": 1, "ts_open": 1_000, "open": 10, "high": 10.2,
        "low": 9.8, "close": 10, "volume": 100, "closed": True,
    }
    save_history_record(
        _record(
            timestamp_ms=1_000,
            order_type="限价单",
            order_direction="做空",
            entry=10,
            tp=9,
            sl=11,
            bars=[old_bar],
        ),
        root=root,
    )
    save_history_record(
        _record(
            timestamp_ms=4_000,
            order_type="不下单",
            bars=[
                {
                    **old_bar,
                    "seq": 1,
                    "ts_open": 3_000,
                    "open": 10,
                    "high": 11.2,
                    "low": 9.8,
                    "close": 11.0,
                },
                old_bar,
            ],
        ),
        root=root,
    )

    entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T0,
        root=root,
    )

    assert all(entry.row_kind == "analysis" for entry in entries)


def test_pending_long_order_is_invalidated_when_entry_bar_hits_stop(tmp_path):
    root = tmp_path / "history"
    old_bar = {
        "seq": 1, "ts_open": 1_000, "open": 10, "high": 10.2,
        "low": 9.8, "close": 10, "volume": 100, "closed": True,
    }
    save_history_record(
        _record(
            timestamp_ms=1_000,
            order_type="限价单",
            order_direction="做多",
            entry=10,
            tp=11,
            sl=9,
            bars=[old_bar],
        ),
        root=root,
    )
    save_history_record(
        _record(
            timestamp_ms=4_000,
            order_type="不下单",
            bars=[
                {
                    **old_bar,
                    "seq": 1,
                    "ts_open": 3_000,
                    "open": 10,
                    "high": 10.2,
                    "low": 8.8,
                    "close": 9.0,
                },
                old_bar,
            ],
        ),
        root=root,
    )

    entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T0,
        root=root,
    )

    assert all(entry.row_kind == "analysis" for entry in entries)


def test_long_stop_requires_a_close_below_sl_after_limit_entry(tmp_path):
    root = tmp_path / "history"
    old_bar = {
        "seq": 1, "ts_open": 1_000, "open": 1.360, "high": 1.365,
        "low": 1.355, "close": 1.361, "volume": 100, "closed": True,
    }
    save_history_record(
        _record(
            timestamp_ms=1_000,
            order_type="限价单",
            order_direction="做多",
            entry=1.361,
            tp=1.450,
            sl=1.347,
            bars=[old_bar],
        ),
        root=root,
    )
    save_history_record(
        _record(
            timestamp_ms=4_000,
            order_type="不下单",
            bars=[
                {
                    **old_bar,
                    "seq": 1,
                    "ts_open": 3_000,
                    "open": 1.390,
                    "high": 1.400,
                    "low": 1.340,
                    "close": 1.398,
                },
                {
                    **old_bar,
                    "seq": 2,
                    "ts_open": 2_000,
                    "open": 1.360,
                    "high": 1.365,
                    "low": 1.358,
                    "close": 1.362,
                },
                old_bar,
            ],
        ),
        root=root,
    )

    entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T0,
        root=root,
    )

    assert all(entry.row_kind == "analysis" for entry in entries)


def test_short_stop_requires_a_close_above_sl_after_limit_entry(tmp_path):
    root = tmp_path / "history"
    old_bar = {
        "seq": 1, "ts_open": 1_000, "open": 10.0, "high": 10.1,
        "low": 9.9, "close": 10.0, "volume": 100, "closed": True,
    }
    save_history_record(
        _record(
            timestamp_ms=1_000,
            order_type="限价单",
            order_direction="做空",
            entry=10.0,
            tp=9.0,
            sl=11.0,
            bars=[old_bar],
        ),
        root=root,
    )
    save_history_record(
        _record(
            timestamp_ms=4_000,
            order_type="不下单",
            bars=[
                {
                    **old_bar,
                    "seq": 1,
                    "ts_open": 3_000,
                    "open": 9.5,
                    "high": 9.8,
                    "low": 8.8,
                    "close": 9.0,
                },
                {
                    **old_bar,
                    "seq": 2,
                    "ts_open": 2_000,
                    "open": 10.0,
                    "high": 11.2,
                    "low": 9.8,
                    "close": 10.5,
                },
                old_bar,
            ],
        ),
        root=root,
    )

    entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T0,
        root=root,
    )
    event = next(entry.trade_event for entry in entries if entry.row_kind == "trade_event")

    assert event is not None
    assert event.outcome == "tp"
    assert event.exit_price == 9.0


def test_t1_ignores_wick_stop_when_close_stays_above_sl(tmp_path):
    root = tmp_path / "history"
    day_one = int(datetime(2026, 8, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
    day_two = day_one + 24 * 60 * 60 * 1000
    old_bar = {
        "seq": 1, "ts_open": day_one, "open": 10, "high": 10.2,
        "low": 9.8, "close": 10, "volume": 100, "closed": True,
    }
    save_history_record(
        _record(
            timestamp_ms=day_one,
            timestamp_iso="2026-08-03T01:00:00+00:00",
            order_type="限价单",
            order_direction="做多",
            entry=10,
            tp=11,
            sl=9,
            bars=[old_bar],
        ),
        root=root,
    )
    save_history_record(
        _record(
            timestamp_ms=day_two + 30 * 60_000,
            order_type="不下单",
            bars=[
                {
                    **old_bar,
                    "seq": 2,
                    "ts_open": day_two + 15 * 60_000,
                    "open": 10,
                    "high": 11.2,
                    "low": 8.8,
                    "close": 9.5,
                },
                {
                    **old_bar,
                    "seq": 2,
                    "ts_open": day_one + 30 * 60_000,
                    "open": 10,
                    "high": 11.2,
                    "low": 8.8,
                    "close": 10.5,
                },
                {
                    **old_bar,
                    "seq": 1,
                    "ts_open": day_one + 15 * 60_000,
                    "open": 10,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                },
                old_bar,
            ],
        ),
        root=root,
    )

    entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T1,
        root=root,
    )
    event = next(entry.trade_event for entry in entries if entry.row_kind == "trade_event")

    assert event is not None
    assert event.outcome == "tp"
    assert event.exit_price == 9.5
    assert event.return_pct == -5.0
    assert [(touch.outcome, touch.price) for touch in event.blocked_touches] == [("tp", 11.0)]


def test_t1_keeps_same_day_target_open_then_records_next_day_stop(tmp_path):
    root = tmp_path / "history"
    day_one = int(datetime(2026, 8, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
    day_two = int((datetime(2026, 8, 3, 1, tzinfo=timezone.utc) + timedelta(days=1)).timestamp() * 1000)
    old_bar = {
        "seq": 1, "ts_open": day_one, "open": 10, "high": 10.1,
        "low": 9.9, "close": 10, "volume": 100, "closed": True,
    }
    save_history_record(
        _record(
            timestamp_ms=day_one,
            order_type="限价单",
            order_direction="做空",
            entry=10,
            tp=9,
            sl=11,
            bars=[old_bar],
        ),
        root=root,
    )
    current_bars = [
        {**old_bar, "seq": 1, "ts_open": day_two + 15 * 60_000, "high": 11.2, "low": 10.5, "close": 11.1},
        {**old_bar, "seq": 2, "ts_open": day_one + 30 * 60_000, "high": 9.8, "low": 8.9, "close": 9.1},
        {**old_bar, "seq": 3, "ts_open": day_one + 15 * 60_000, "high": 10.1, "low": 9.8, "close": 10.0},
        old_bar,
    ]
    save_history_record(
        _record(timestamp_ms=day_two + 30 * 60_000, order_type="不下单", bars=current_bars),
        root=root,
    )

    entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T1,
        root=root,
    )
    event_row = next(entry for entry in entries if entry.row_kind == "trade_event")
    assert event_row.trade_event is not None
    event = event_row.trade_event

    assert event.outcome == "sl"
    assert event.exit_price == 11.1
    assert event.return_pct == -11.0
    assert [(touch.outcome, touch.price) for touch in event.blocked_touches] == [("tp", 9.0)]


def test_t0_short_plan_uses_the_short_direction_for_target_and_return(tmp_path):
    root = tmp_path / "history"
    old_bar = {
        "seq": 1, "ts_open": 1_000, "open": 10, "high": 10.1,
        "low": 9.9, "close": 10, "volume": 100, "closed": True,
    }
    save_history_record(
        _record(
            timestamp_ms=1_000,
            order_type="限价单",
            order_direction="做空",
            entry=10,
            tp=9,
            sl=11,
            bars=[old_bar],
        ),
        root=root,
    )
    save_history_record(
        _record(
            timestamp_ms=4_000,
            order_type="不下单",
            bars=[
                {**old_bar, "seq": 1, "ts_open": 3_000, "high": 9.5, "low": 8.8, "close": 9.0},
                {**old_bar, "seq": 2, "ts_open": 2_000, "high": 10.1, "low": 9.9},
                old_bar,
            ],
        ),
        root=root,
    )

    entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T0,
        root=root,
    )
    event = next(entry.trade_event for entry in entries if entry.row_kind == "trade_event")

    assert event is not None
    assert event.source_direction == "做空"
    assert event.outcome == "tp"
    assert event.exit_price == 9.0
    assert event.return_pct == 10.0


def test_trade_event_uses_later_kline_evidence_outside_the_displayed_date_range(tmp_path):
    root = tmp_path / "history"
    old_bar = {
        "seq": 1, "ts_open": 1_000, "open": 10, "high": 10.2,
        "low": 9.8, "close": 10, "volume": 100, "closed": True,
    }
    save_history_record(
        _record(
            timestamp_ms=1_000,
            order_type="限价单",
            order_direction="做多",
            entry=10,
            tp=11,
            sl=9,
            bars=[old_bar],
        ),
        root=root,
    )
    save_history_record(
        _record(
            timestamp_ms=4_000,
            order_type="不下单",
            bars=[
                {**old_bar, "seq": 1, "ts_open": 3_000, "high": 11.2, "low": 10.4},
                {**old_bar, "seq": 2, "ts_open": 2_000, "high": 10.2, "low": 9.9},
                old_bar,
            ],
        ),
        root=root,
    )

    entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        start_ms=1_000,
        end_ms=1_000,
        settlement_mode=SETTLEMENT_T0,
        root=root,
    )

    assert [entry.row_kind for entry in entries] == ["trade_event", "analysis"]
    assert entries[0].trade_event is not None
    assert entries[0].trade_event.outcome == "tp"


def test_t1_uses_the_archived_market_timezone_for_same_day_settlement(tmp_path):
    root = tmp_path / "history"
    plan_at = int(datetime(2026, 8, 4, 23, tzinfo=timezone.utc).timestamp() * 1000)
    entry_bar_at = plan_at + 15 * 60_000
    target_at = int(datetime(2026, 8, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
    old_bar = {
        "seq": 1, "ts_open": plan_at, "open": 10, "high": 10.1,
        "low": 9.9, "close": 10, "volume": 100, "closed": True,
    }
    save_history_record(
        _record(
            timestamp_ms=plan_at,
            timestamp_iso="2026-08-04T19:00:00-04:00",
            order_type="限价单",
            order_direction="做多",
            entry=10,
            tp=11,
            sl=9,
            bars=[old_bar],
        ),
        root=root,
    )
    save_history_record(
        _record(
            timestamp_ms=target_at + 15 * 60_000,
            order_type="不下单",
            bars=[
                {**old_bar, "seq": 1, "ts_open": target_at, "high": 11.2, "low": 10.1},
                {**old_bar, "seq": 2, "ts_open": entry_bar_at, "high": 10.1, "low": 9.9},
                old_bar,
            ],
        ),
        root=root,
    )

    entries = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="15m",
        settlement_mode=SETTLEMENT_T1,
        root=root,
    )
    event = next(entry.trade_event for entry in entries if entry.row_kind == "trade_event")

    assert event is not None
    assert event.outcome is None
    assert [(touch.outcome, touch.price) for touch in event.blocked_touches] == [("tp", 11.0)]


def test_unsettlement_rule_does_not_create_trade_event(tmp_path):
    root = tmp_path / "history"
    old_bar = {
        "seq": 1, "ts_open": 1_000, "open": 10, "high": 10.2,
        "low": 9.8, "close": 10, "volume": 100, "closed": True,
    }
    save_history_record(
        _record(
            timestamp_ms=1_000,
            order_type="限价单",
            order_direction="做多",
            entry=10,
            tp=11,
            sl=9,
            bars=[old_bar],
        ),
        root=root,
    )
    save_history_record(
        _record(
            timestamp_ms=4_000,
            order_type="不下单",
            bars=[
                {**old_bar, "seq": 1, "ts_open": 3_000, "high": 11.2, "low": 10.4},
                {**old_bar, "seq": 2, "ts_open": 2_000, "high": 10.2, "low": 9.9},
                old_bar,
            ],
        ),
        root=root,
    )

    entries = list_history_entries(symbol="NASDAQ:AAPL", timeframe="15m", root=root)

    assert len(entries) == 2
    assert all(entry.row_kind == "analysis" for entry in entries)


def test_price_change_uses_previous_symbol_record_across_timeframes(tmp_path):
    root = tmp_path / "history"
    save_history_record(_record(timeframe="15m", timestamp_ms=1_000, close=10), root=root)
    save_history_record(_record(timeframe="1h", timestamp_ms=2_000, close=20), root=root)

    entries = list_history_entries(symbol="NASDAQ:AAPL", root=root)

    newest, oldest = entries
    assert newest.price_change == 10
    assert newest.price_change_pct == 100.0
    assert oldest.price_change is None

    filtered = list_history_entries(
        symbol="NASDAQ:AAPL",
        timeframe="1h",
        root=root,
    )
    assert filtered[0].price_change == 10
    assert filtered[0].price_change_pct == 100.0


def test_history_panel_uses_compact_timestamp_and_market_cycle_header(qtbot):
    from datetime import datetime

    from pa_agent.gui.history_panel import HistoryPanel

    panel = HistoryPanel()
    qtbot.addWidget(panel)
    timestamp_ms = int(datetime(2026, 7, 24, 10, 11, 12).timestamp() * 1000)

    assert panel._format_timestamp(timestamp_ms) == "07-24 / 10:11:12"
    assert panel._table.horizontalHeaderItem(8).text() == "趋势 / 市场周期"
    assert panel._table.horizontalHeaderItem(6).text() == "支撑"
    assert panel._table.horizontalHeaderItem(7).text() == "阻力"
    assert panel._table.columnWidth(4) == panel._table.columnWidth(3)
    assert panel._table.columnWidth(5) == panel._table.columnWidth(3)
    assert not panel._entry_flexible_check.isEnabled()
    assert not panel._entry_tolerance_spin.isEnabled()


def test_history_panel_persists_entry_tolerance_setting(qtbot, monkeypatch, tmp_path):
    from pa_agent.gui import history_panel
    from pa_agent.records.trade_rules import InstrumentTradeRuleStore, TradeEntryOverrideStore

    monkeypatch.setattr(history_panel, "list_history_entries", lambda **_kwargs: [])
    rules_path = tmp_path / "instrument_trade_rules.json"
    override_path = tmp_path / "trade_entry_overrides.json"
    rules = InstrumentTradeRuleStore(rules_path)
    panel = history_panel.HistoryPanel(
        trade_rule_store=rules,
        entry_override_store=TradeEntryOverrideStore(override_path),
    )
    qtbot.addWidget(panel)
    panel.set_symbol("SZ.513090")

    panel._entry_tolerance_spin.setValue(3)
    panel._entry_flexible_check.setChecked(True)

    assert rules.entry_tolerance_ticks_for("SZ.513090") == 3
    restored = InstrumentTradeRuleStore(rules_path)
    assert restored.entry_tolerance_ticks_for("SZ.513090") == 3


def test_history_panel_swaps_support_and_resistance_values(qtbot, monkeypatch):
    from dataclasses import replace
    from pathlib import Path

    from pa_agent.gui import history_panel
    from pa_agent.records.history_analysis import HistoryAnalysisEntry

    entry = HistoryAnalysisEntry(
        path=Path("record.json"),
        symbol="SH.600519",
        timeframe="15m",
        timestamp_ms=1_000,
        timestamp_iso="2026-07-27T00:00:01+08:00",
        decision_label="做多单",
        current_price=100.0,
        price_change=1.0,
        price_change_pct=1.0,
        entry_price=99.0,
        take_profit_price=101.0,
        stop_loss_price=98.0,
        trend_label="上涨",
        trend_direction="bullish",
        cycle_label="上涨交易区间",
        resistance_levels=("101-102",),
        support_levels=("98-99",),
    )
    bearish_entry = replace(
        entry,
        price_change=-1.0,
        price_change_pct=-1.0,
        trend_label="下跌",
        trend_direction="bearish",
    )
    spike_entry = replace(entry, cycle_label="尖峰")
    monkeypatch.setattr(
        history_panel,
        "list_history_entries",
        lambda **_kwargs: [entry, bearish_entry, spike_entry],
    )
    panel = history_panel.HistoryPanel()
    qtbot.addWidget(panel)
    panel.set_symbol("SH.600519")

    assert panel._table.horizontalHeaderItem(0).text() == "日期 / 时间 / 周期"
    assert panel._table.item(0, 0).text().endswith(" / 15m")
    assert panel._table.item(0, 6).text() == "98-99"
    assert panel._table.item(0, 7).text() == "101-102"
    assert "#ff5353" not in panel._table.cellWidget(0, 8).text().lower()
    assert "#00c087" not in panel._table.cellWidget(1, 8).text().lower()
    assert panel._table.cellWidget(0, 8).styleSheet() == "color: #E8ECF1;"
    assert panel._table.cellWidget(1, 8).styleSheet() == "color: #E8ECF1;"
    assert 'color:#FF4757' in panel._table.cellWidget(2, 8).text()
    assert "#FF5353" in panel._table.cellWidget(0, 2).text()
    assert "#00C087" in panel._table.cellWidget(1, 2).text()
    assert panel._table.selectionMode().name == "NoSelection"


def test_history_panel_shows_trade_event_above_its_source_record(qtbot, monkeypatch):
    from dataclasses import replace
    from pathlib import Path

    from pa_agent.gui import history_panel
    from pa_agent.records.history_analysis import HistoryAnalysisEntry, HistoryTradeEvent, TradeTouch
    from pa_agent.records.trade_rules import SETTLEMENT_T0

    source = HistoryAnalysisEntry(
        path=Path("source-record.json"),
        symbol="SH.600519",
        timeframe="15m",
        timestamp_ms=1_000,
        timestamp_iso="2026-07-27T00:00:01+08:00",
        decision_label="做多单",
        current_price=101.0,
        price_change=1.0,
        price_change_pct=1.0,
        entry_price=99.0,
        take_profit_price=101.0,
        stop_loss_price=98.0,
        trend_label="上涨",
        trend_direction="bullish",
        cycle_label="上涨交易区间",
        resistance_levels=(),
        support_levels=(),
    )
    event = HistoryTradeEvent(
        source_path=source.path,
        source_timestamp_ms=source.timestamp_ms,
        source_direction="做多",
        settlement_mode=SETTLEMENT_T0,
        entry_price=99.0,
        take_profit_price=101.0,
        stop_loss_price=98.0,
        entry_timestamp_ms=1_500,
        timestamp_ms=2_000,
        outcome="tp",
        exit_price=101.0,
        return_pct=2.02,
        blocked_touches=(
            TradeTouch(outcome="tp", timestamp_ms=1_800, price=101.0),
            TradeTouch(outcome="tp", timestamp_ms=1_900, price=101.0),
        ),
    )
    event_row = replace(
        source,
        timestamp_ms=event.timestamp_ms,
        decision_label="盈损",
        current_price=None,
        price_change=None,
        price_change_pct=None,
        entry_price=None,
        take_profit_price=None,
        stop_loss_price=None,
        trend_label="",
        trend_direction=None,
        cycle_label="",
        row_kind="trade_event",
        trade_event=event,
    )
    monkeypatch.setattr(history_panel, "list_history_entries", lambda **_kwargs: [event_row, source])
    panel = history_panel.HistoryPanel()
    qtbot.addWidget(panel)
    panel.set_symbol("SH.600519")

    assert "盈损" in panel._table.cellWidget(0, 0).text()
    assert "最终止盈" in panel._table.cellWidget(0, 1).text()
    event_html = panel._trade_event_html(event)
    assert "关联" not in event_html
    assert event_html.count("触及止盈") == 1
    assert "..." in event_html
    assert "止盈" not in panel._table.cellWidget(1, 2).text()
    assert panel._table.item(0, 0).data(Qt.ItemDataRole.UserRole) == "source-record.json"

    with qtbot.waitSignal(panel.record_selected) as signal:
        panel._table.itemClicked.emit(panel._table.item(0, 0))
    assert signal.args == ["source-record.json"]


def test_history_panel_date_filter_enables_calendar_and_applies_range(qtbot, monkeypatch):
    from PyQt6.QtCore import QDate

    from pa_agent.gui import history_panel

    calls: list[dict] = []

    def fake_list_history_entries(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(history_panel, "list_history_entries", fake_list_history_entries)
    panel = history_panel.HistoryPanel()
    qtbot.addWidget(panel)
    panel.set_symbol("SZ.159732")

    assert not panel._start_date.isEnabled()
    assert not panel._end_date.isEnabled()

    panel._date_filter_check.setChecked(True)

    assert panel._start_date.isEnabled()
    assert panel._end_date.isEnabled()
    assert panel._start_date.calendarPopup()
    assert panel._end_date.calendarPopup()

    panel._start_date.setDate(QDate(2026, 7, 20))
    panel._end_date.setDate(QDate(2026, 7, 24))

    assert calls[-1]["symbol"] == "SZ.159732"
    assert calls[-1]["start_ms"] == panel._date_to_start_ms(QDate(2026, 7, 20))
    assert calls[-1]["end_ms"] == panel._date_to_end_ms(QDate(2026, 7, 24))


def test_history_panel_date_filter_keeps_a_valid_range(qtbot, monkeypatch):
    from PyQt6.QtCore import QDate

    from pa_agent.gui import history_panel

    monkeypatch.setattr(history_panel, "list_history_entries", lambda **_kwargs: [])
    panel = history_panel.HistoryPanel()
    qtbot.addWidget(panel)
    panel._date_filter_check.setChecked(True)

    next_day = QDate.currentDate().addDays(1)
    panel._start_date.setDate(next_day)
    assert panel._end_date.date() == next_day

    panel._end_date.setDate(QDate(2026, 7, 20))
    assert panel._start_date.date() == QDate(2026, 7, 20)


def test_history_panel_calendar_cells_fit_two_digit_dates(qtbot):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import (
        QApplication,
        QStyle,
        QStyleOptionSpinBox,
        QStyleOptionViewItem,
        QTableView,
    )

    from pa_agent.gui.history_panel import HistoryPanel
    from pa_agent.gui.theme.apply import apply_theme

    app = QApplication.instance()
    assert app is not None
    apply_theme(app)
    panel = HistoryPanel()
    qtbot.addWidget(panel)
    panel._date_filter_check.setChecked(True)
    panel.show()
    panel._start_date.show()
    app.processEvents()

    option = QStyleOptionSpinBox()
    panel._start_date.initStyleOption(option)
    arrow = panel._start_date.style().subControlRect(
        QStyle.ComplexControl.CC_SpinBox,
        option,
        QStyle.SubControl.SC_SpinBoxDown,
        panel._start_date,
    )
    QTest.mouseClick(panel._start_date, Qt.MouseButton.LeftButton, pos=arrow.center())
    app.processEvents()

    table = panel._start_date.calendarWidget().findChild(QTableView)
    assert table is not None
    model = table.model()
    date_ten = next(
        model.index(row, column)
        for row in range(model.rowCount())
        for column in range(model.columnCount())
        if model.data(model.index(row, column), Qt.ItemDataRole.DisplayRole) == 10
    )
    item_width = table.itemDelegate().sizeHint(QStyleOptionViewItem(), date_ten).width()
    assert item_width <= table.columnWidth(date_ten.column())
