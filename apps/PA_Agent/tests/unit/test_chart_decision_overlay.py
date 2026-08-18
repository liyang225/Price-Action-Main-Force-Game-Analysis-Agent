"""Tests for chart continuity overlay enrichment."""
from __future__ import annotations

from types import SimpleNamespace

from pa_agent.ai import decision_continuity
from pa_agent.ai.decision_continuity import DEFAULT_STRUCTURE_FLIP_COOLDOWN_BARS
from pa_agent.gui.chart_decision_overlay import enrich_decision_for_chart_overlay


def _bar(close: float, *, high: float | None = None, low: float | None = None) -> SimpleNamespace:
    hi = high if high is not None else close + 1.0
    lo = low if low is not None else close - 1.0
    return SimpleNamespace(open=close, high=hi, low=lo, close=close, closed=True)


def _frame() -> SimpleNamespace:
    return SimpleNamespace(
        symbol="XAUUSDm",
        timeframe="15m",
        snapshot_ts_local_ms=1_700_000_000_000,
        bars=[_bar(2650.0)],
    )


def _previous_record(*, timestamp_ms: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        meta=SimpleNamespace(
            timestamp_local_iso="2026-06-30 09:00:00",
            timestamp_local_ms=timestamp_ms,
        ),
        stage2_decision={
            "decision": {
                "order_type": "限价单",
                "order_direction": "做多",
                "entry_price": 2640.0,
                "take_profit_price": 2660.0,
                "stop_loss_price": 2630.0,
            },
            "terminal": {"outcome": "trade", "node_id": "11.3", "label": "限价单"},
        },
    )


def test_enrich_keeps_previous_prices_on_wait_no_order() -> None:
    current = {
        "order_type": "不下单",
        "order_direction": None,
        "entry_price": None,
        "take_profit_price": None,
        "stop_loss_price": None,
        "reasoning": "上一轮方案未失效，继续等待触发。",
    }
    stage2 = {
        "decision": current,
        "terminal": {"outcome": "wait", "node_id": "9.0", "label": "等待"},
    }
    out = enrich_decision_for_chart_overlay(
        current,
        stage2_full=stage2,
        frame=_frame(),
        stage1_json={"direction": "bullish"},
        previous_record=_previous_record(),
        cooldown_bars=DEFAULT_STRUCTURE_FLIP_COOLDOWN_BARS,
    )
    assert out.get("chart_overlay_active") is True
    assert out["entry_price"] == 2640.0
    assert out["take_profit_price"] == 2660.0
    assert out["stop_loss_price"] == 2630.0
    assert out["order_type"] == "不下单"


def test_enrich_skips_when_previous_plan_invalidated() -> None:
    frame = _frame()
    frame.bars[0] = _bar(2625.0, high=2631.0, low=2624.0)
    current = {"order_type": "不下单", "entry_price": None, "take_profit_price": None, "stop_loss_price": None}
    out = enrich_decision_for_chart_overlay(
        current,
        stage2_full={"terminal": {"outcome": "wait"}},
        frame=frame,
        stage1_json={"direction": "bullish"},
        previous_record=_previous_record(),
    )
    assert "chart_overlay_active" not in out


def test_enrich_skips_previous_plan_older_than_one_day() -> None:
    frame = _frame()
    previous = _previous_record(
        timestamp_ms=frame.snapshot_ts_local_ms - 24 * 60 * 60 * 1000 - 1
    )
    current = {
        "order_type": "不下单",
        "entry_price": None,
        "take_profit_price": None,
        "stop_loss_price": None,
    }

    out = enrich_decision_for_chart_overlay(
        current,
        stage2_full={"terminal": {"outcome": "wait"}},
        frame=frame,
        stage1_json={"direction": "bullish"},
        previous_record=previous,
    )

    assert "chart_overlay_active" not in out


def test_enrich_uses_current_prices_when_still_trading() -> None:
    current = {
        "order_type": "限价单",
        "order_direction": "做多",
        "entry_price": 2700.0,
        "take_profit_price": 2720.0,
        "stop_loss_price": 2680.0,
    }
    out = enrich_decision_for_chart_overlay(
        current,
        frame=_frame(),
        stage1_json={"direction": "bullish"},
        previous_record=_previous_record(),
    )
    assert out.get("chart_overlay_active") is not True
    assert out["entry_price"] == 2700.0


def test_history_overlay_never_uses_trade_csv_fallback(monkeypatch) -> None:
    csv_plan = {
        "record_time": "",
        "order_type": "限价单",
        "order_direction": "做多",
        "entry_price": "2640",
        "take_profit_price": "2660",
        "stop_loss_price": "2630",
    }
    monkeypatch.setattr(
        decision_continuity,
        "load_last_trade_csv_row",
        lambda *_args, **_kwargs: csv_plan,
    )
    current = {
        "order_type": "不下单",
        "entry_price": None,
        "take_profit_price": None,
        "stop_loss_price": None,
    }

    out = enrich_decision_for_chart_overlay(
        current,
        stage2_full={"terminal": {"outcome": "wait"}},
        frame=_frame(),
        stage1_json={"direction": "bullish"},
        allow_trade_csv_fallback=False,
    )

    assert "chart_overlay_active" not in out
    assert out["entry_price"] is None
