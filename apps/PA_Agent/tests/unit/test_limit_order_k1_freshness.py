"""Tests for limit-order vs K1 price freshness validation."""
from __future__ import annotations

import json

from pa_agent.ai.json_validator import JsonValidator, Ok
from pa_agent.config.settings import ValidationSettings
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.util.trade_metrics import validate_limit_order_k1_freshness

from tests.unit.test_trade_metrics_validation import _stage2_trade_obj


def _frame_k1(*, high: float, low: float, close: float) -> KlineFrame:
    return KlineFrame(
        symbol="XAUUSD",
        timeframe="5m",
        bars=(
            KlineBar(
                seq=1,
                ts_open=1.0,
                open=close,
                high=high,
                low=low,
                close=close,
                volume=1,
                closed=True,
            ),
            KlineBar(
                seq=2,
                ts_open=0.0,
                open=100.0,
                high=102.0,
                low=98.0,
                close=101.0,
                volume=1,
                closed=True,
            ),
        ),
        indicators=IndicatorBundle(ema20=(100.0, 100.0), atr14=(2.0, 2.0)),
        snapshot_ts_local_ms=1,
    )


def test_short_limit_rejected_when_k1_exceeds_entry_and_stop() -> None:
    decision = {
        "order_type": "限价单",
        "order_direction": "做空",
        "entry_price": 2650.0,
        "stop_loss_price": 2660.0,
        "take_profit_price": 2640.0,
    }
    frame = _frame_k1(high=2662.0, low=2648.0, close=2661.0)
    errors = validate_limit_order_k1_freshness(decision, frame)
    assert len(errors) >= 2
    assert any("entry" in e for e in errors)
    assert any("stop" in e for e in errors)


def test_short_limit_ok_when_k1_below_entry() -> None:
    decision = {
        "order_type": "限价单",
        "order_direction": "做空",
        "entry_price": 2650.0,
        "stop_loss_price": 2660.0,
        "take_profit_price": 2640.0,
    }
    frame = _frame_k1(high=2648.0, low=2642.0, close=2645.0)
    assert not validate_limit_order_k1_freshness(decision, frame)


def test_validator_coerces_stale_short_limit_to_no_order() -> None:
    validator = JsonValidator(ValidationSettings(normalization_mode="lenient"))
    obj = _stage2_trade_obj(
        order_type="限价单",
        order_direction="做空",
        entry_price=101.0,
        take_profit_price=98.0,
        stop_loss_price=103.0,
        estimated_win_rate=55,
        entry_basis_bar=None,
        entry_basis_extreme=None,
    )
    obj["bar_analysis"]["always_in"] = "short"
    obj["bar_analysis"]["entry_bar"] = {
        "bar": None,
        "strength": "not_triggered",
        "follow_through": "pending",
        "still_valid": True,
        "freshness": "pending",
    }
    frame = _frame_k1(high=104.0, low=100.0, close=103.5)
    result = validator.validate(
        "stage2",
        json.dumps(obj),
        decision_stance="aggressive",
        kline_frame=frame,
    )
    assert isinstance(result, Ok)
    assert result.obj["decision"]["order_type"] == "不下单"
