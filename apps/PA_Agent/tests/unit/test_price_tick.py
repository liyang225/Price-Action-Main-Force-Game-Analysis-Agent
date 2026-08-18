"""Tests for breakout tick inference and entry_price normalization."""
from __future__ import annotations

from pa_agent.ai.json_validator import JsonValidator
from pa_agent.ai.stage2_normalizer import normalize_stage2
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.util.price_tick import (
    infer_price_tick_from_frame,
    normalize_breakout_basis_extreme,
    normalize_breakout_entry_price,
    round_to_tick,
)
from tests.fixtures.validators import schema_test_validator

import json


def _frame(high: float = 104.0) -> KlineFrame:
    return KlineFrame(
        symbol="XAUUSD",
        timeframe="5m",
        bars=(
            KlineBar(
                seq=1,
                ts_open=1.0,
                open=100.0,
                high=high,
                low=99.0,
                close=103.0,
                volume=1,
                closed=True,
            ),
        ),
        indicators=IndicatorBundle(ema20=(100.0,), atr14=(2.0,)),
        snapshot_ts_local_ms=1,
    )


def test_infer_tick_from_three_decimal_prices() -> None:
    frame = _frame(high=4556.595)
    assert infer_price_tick_from_frame(frame) == 0.001


def test_normalize_breakout_entry_at_high_bumps_up() -> None:
    frame = _frame(high=4556.595)
    decision = {
        "order_type": "突破单",
        "order_direction": "做多",
        "entry_basis_bar": "K1",
        "entry_basis_extreme": "high",
        "entry_price": 4556.595,
    }
    assert normalize_breakout_entry_price(decision, kline_frame=frame)
    assert decision["entry_price"] == round_to_tick(4556.595 + 0.001, 0.001)


def test_normalize_short_breakout_extreme_high_to_low() -> None:
    decision = {
        "order_type": "突破单",
        "order_direction": "做空",
        "entry_basis_extreme": "high",
        "entry_basis_bar": "K3",
        "entry_price": 3.42,
    }
    assert normalize_breakout_basis_extreme(decision)
    assert decision["entry_basis_extreme"] == "low"


def test_stage2_normalizer_passes_breakout_price_check() -> None:
    frame = _frame(high=104.0)
    obj = normalize_stage2(
        {
            "decision": {
                "order_type": "突破单",
                "order_direction": "做多",
                "entry_basis_bar": "K1",
                "entry_basis_extreme": "high",
                "entry_price": 104.0,
                "take_profit_price": 120.0,
                "stop_loss_price": 99.0,
                "estimated_win_rate": 55,
            },
        },
        kline_frame=frame,
    )
    assert obj["decision"]["entry_price"] > 104.0
    msgs = JsonValidator._check_breakout_price_extreme(obj, frame)
    assert msgs == []
