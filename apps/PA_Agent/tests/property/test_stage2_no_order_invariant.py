"""Property-based tests for Stage 2 不下单 ↔ null invariant (task 8.5 / PR3)."""
from __future__ import annotations

import json
import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st
from pa_agent.ai.json_validator import JsonValidator, Ok, ValidationError

from tests.fixtures.validators import schema_test_validator

validator = schema_test_validator()

_ORDER_TYPES_WITH_TRADE = ["限价单", "突破单", "市价单"]
_PRICE_FIELDS = [
    "entry_price",
    "take_profit_price",
    "take_profit_price_2",
    "stop_loss_price",
]
_DIRECTION_FIELD = "order_direction"


def _base_decision(**overrides) -> dict:
    d = {
        "order_type": "不下单",
        "order_direction": None,
        "entry_price": None,
        "take_profit_price": None,
        "take_profit_price_2": None,
        "stop_loss_price": None,
        "reasoning": "test",
        "diagnosis_confidence": 40,
        "diagnosis_confidence_reasoning": "test",
        "trade_confidence": 30,
        "trade_confidence_reasoning": "test",
        "estimated_win_rate": None,
        "estimated_win_rate_reasoning": "test",
        "key_factors": [],
        "watch_points": [],
        "risk_assessment": "test",
        "invalidation_condition": "test",
    }
    d.update(overrides)
    return d


def _base_stage2(decision: dict) -> dict:
    is_no_order = decision.get("order_type") == "不下单"
    trace = [
        {
            "node_id": "10.3",
            "question": "交易者方程是否通过？",
            "answer": "否" if is_no_order else "是",
            "reason": "test",
            "bar_range": "K2-K1",
        }
    ]
    if not is_no_order:
        trace.append(
            {
                "node_id": "11.1",
                "question": "是趋势 / 尖峰状态吗？",
                "answer": "是",
                "reason": "test",
                "bar_range": "K2-K1",
            }
        )
    obj = {
        "decision": decision,
        "diagnosis_summary": {
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "key_signals": [],
        },
        "decision_trace": trace,
        "terminal": {
            "node_id": "10.3" if is_no_order else "11.1",
            "outcome": "reject" if is_no_order else "trade",
            "label": "test",
        },
    }
    if not is_no_order:
        obj["bar_analysis"] = {
            "always_in": "long",
            "last_closed_bar": "K1",
            "bar_type": "trend_bull",
            "signal_bar": {
                "bar": "K2",
                "quality": "strong",
                "pattern": "H1",
                "reason": "test",
            },
            "entry_bar": {
                "bar": "K1",
                "strength": "strong",
                "follow_through": True,
                "still_valid": True,
                "freshness": "fresh",
            },
            "second_entry": {
                "is_second_entry": False,
                "type": "none",
            },
        }
    return obj


# ── 不下单 side ────────────────────────────────────────────────────────────────

def test_no_order_all_null_accepted():
    """不下单 with all price fields null is accepted.

    **Validates: Requirements PR3.1**
    """
    obj = _base_stage2(_base_decision(order_type="不下单"))
    result = validator.validate("stage2", json.dumps(obj))
    assert isinstance(result, Ok), f"Expected Ok, got {result}"


@given(
    price_val=st.one_of(
        st.floats(allow_nan=False, allow_infinity=False),
        st.integers(),
        st.just(0),
        st.just(0.0),
    )
)
@h_settings(max_examples=100)
def test_no_order_with_non_null_price_rejected(price_val) -> None:
    """不下单 with any non-null price field is rejected as category c.

    **Validates: Requirements PR3.1**
    """
    for field in _PRICE_FIELDS:
        decision = _base_decision(order_type="不下单", **{field: price_val})
        obj = _base_stage2(decision)
        result = validator.validate("stage2", json.dumps(obj))
        assert isinstance(result, ValidationError), (
            f"Expected ValidationError for {field}={price_val!r}, got Ok"
        )
        assert result.category == "c", f"Expected category c, got {result.category!r}"


# ── 有下单 side ────────────────────────────────────────────────────────────────

@given(order_type=st.sampled_from(_ORDER_TYPES_WITH_TRADE))
@h_settings(max_examples=50)
def test_with_order_all_fields_present_accepted(order_type: str) -> None:
    """有下单 with all required fields present is accepted.

    **Validates: Requirements PR3.1**
    """
    decision = _base_decision(
        order_type=order_type,
        order_direction="做多",
        entry_price=2650.0,
        take_profit_price=2700.0,
        take_profit_price_2=2760.0,
        stop_loss_price=2620.0,
        estimated_win_rate=52,
    )
    if order_type == "突破单":
        decision.update(
            {
                "entry_basis_bar": "K2",
                "entry_basis_extreme": "high",
                "entry_rule": "做多突破单挂在K2高点上方1跳动",
            }
        )
    obj = _base_stage2(decision)
    result = validator.validate("stage2", json.dumps(obj))
    assert isinstance(result, Ok), f"Expected Ok for {order_type}, got {result}"


def test_breakout_order_requires_extreme_basis() -> None:
    decision = _base_decision(
        order_type="突破单",
        order_direction="做多",
        entry_price=2650.0,
        take_profit_price=2700.0,
        take_profit_price_2=2760.0,
        stop_loss_price=2620.0,
        estimated_win_rate=52,
    )
    obj = _base_stage2(decision)
    result = validator.validate("stage2", json.dumps(obj))
    assert isinstance(result, ValidationError)
    assert result.category == "c"
    assert "entry_basis_bar" in result.missing_fields


def test_breakout_order_direction_extreme_mismatch_auto_corrected() -> None:
    """Wrong entry_basis_extreme is aligned in normalize_stage2 before validation."""
    decision = _base_decision(
        order_type="突破单",
        order_direction="做多",
        entry_price=2650.0,
        take_profit_price=2700.0,
        take_profit_price_2=2760.0,
        stop_loss_price=2620.0,
        estimated_win_rate=52,
        entry_basis_bar="K2",
        entry_basis_extreme="low",
        entry_rule="模型误写 low，程序应改为 high",
    )
    obj = _base_stage2(decision)
    result = validator.validate("stage2", json.dumps(obj))
    assert isinstance(result, Ok)
    assert result.obj["decision"]["entry_basis_extreme"] == "high"


@given(order_type=st.sampled_from(_ORDER_TYPES_WITH_TRADE))
@h_settings(max_examples=50)
def test_with_order_null_price_rejected(order_type: str) -> None:
    """有下单 with null entry_price is rejected as category c.

    **Validates: Requirements PR3.1**
    """
    decision = _base_decision(
        order_type=order_type,
        order_direction="做多",
        entry_price=None,  # must not be null for 有下单
        take_profit_price=2700.0,
        take_profit_price_2=2760.0,
        stop_loss_price=2620.0,
    )
    obj = _base_stage2(decision)
    result = validator.validate("stage2", json.dumps(obj))
    assert isinstance(result, ValidationError), (
        f"Expected ValidationError for {order_type} with null entry_price, got Ok"
    )
    assert result.category == "c"
