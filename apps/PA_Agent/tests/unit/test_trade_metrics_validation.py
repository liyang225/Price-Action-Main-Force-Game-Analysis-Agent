"""Tests for programmatic RR / trader-equation validation."""
from __future__ import annotations

import json

from pa_agent.ai.json_validator import Ok, ValidationError
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.util.trade_metrics import (
    compute_risk_reward,
    passes_trader_equation,
    validate_order_trade_metrics,
)

from tests.fixtures.validators import schema_test_validator

validator = schema_test_validator()


def _frame() -> KlineFrame:
    return KlineFrame(
        symbol="XAUUSD",
        timeframe="5m",
        bars=(
            KlineBar(seq=1, ts_open=1.0, open=101.0, high=104.0, low=100.0, close=103.0, volume=1, closed=True),
            KlineBar(seq=2, ts_open=0.0, open=100.0, high=102.0, low=98.0, close=101.0, volume=1, closed=True),
        ),
        indicators=IndicatorBundle(ema20=(100.0, 100.0), atr14=(2.0, 2.0)),
        snapshot_ts_local_ms=1,
    )


def _stage2_trade_obj(**decision_overrides) -> dict:
    decision = {
        "order_type": "突破单",
        "order_direction": "做多",
        "entry_price": 102.1,
        "take_profit_price": 106.5,
        "take_profit_price_2": 112.0,
        "stop_loss_price": 100.0,
        "reasoning": "test",
        "diagnosis_confidence": 60,
        "diagnosis_confidence_reasoning": "test",
        "trade_confidence": 50,
        "trade_confidence_reasoning": "test",
        "estimated_win_rate": 60,
        "estimated_win_rate_reasoning": "test",
        "key_factors": [],
        "watch_points": [],
        "risk_assessment": "test",
        "invalidation_condition": "test",
        "entry_basis_bar": "K2",
        "entry_basis_extreme": "high",
        "entry_rule": "K2高点上方1跳动",
    }
    decision.update(decision_overrides)
    return {
        "decision": decision,
        "diagnosis_summary": {
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "key_signals": [],
        },
        "bar_analysis": {
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
            "second_entry": {"is_second_entry": False, "type": "none"},
        },
        "decision_trace": [
            {
                "node_id": "10.3",
                "question": "交易者方程是否通过？",
                "answer": "是",
                "reason": "test",
                "bar_range": "K2-K1",
            },
            {
                "node_id": "11.1",
                "question": "趋势？",
                "answer": "是",
                "reason": "test",
                "bar_range": "K2-K1",
            },
        ],
        "terminal": {"node_id": "11.1", "outcome": "trade", "label": "test"},
    }


def test_user_screenshot_prices_fail_validation() -> None:
    """0.81:1 with 47% win rate must be rejected when placing an order."""
    decision = {
        "order_type": "突破单",
        "order_direction": "做多",
        "entry_price": 4527.4,
        "take_profit_price": 4529.5,
        "stop_loss_price": 4524.9,
        "estimated_win_rate": 47,
        "reasoning": "test",
        "diagnosis_confidence": 60,
        "diagnosis_confidence_reasoning": "test",
        "trade_confidence": 50,
        "trade_confidence_reasoning": "test",
        "estimated_win_rate_reasoning": "test",
        "key_factors": [],
        "watch_points": [],
        "risk_assessment": "test",
        "invalidation_condition": "test",
        "entry_basis_bar": "K2",
        "entry_basis_extreme": "high",
        "entry_rule": "K2高点上方1跳动",
    }
    errors = validate_order_trade_metrics(decision, decision_stance="aggressive")
    assert errors
    rr = compute_risk_reward(4527.4, 4529.5, 4524.9, "做多")
    assert rr is not None
    assert rr["ratio"] < 1.0
    assert not passes_trader_equation(47, rr["risk"], rr["reward"])


def test_good_trade_passes_aggressive_stance() -> None:
    decision = {
        "order_type": "限价单",
        "order_direction": "做多",
        "entry_price": 2650.0,
        "take_profit_price": 2690.0,
        "take_profit_price_2": 2760.0,
        "stop_loss_price": 2620.0,
        "estimated_win_rate": 52,
    }
    assert not validate_order_trade_metrics(decision, decision_stance="aggressive")


def test_high_rr_allowed_when_trader_equation_passes() -> None:
    """2:1 reward is allowed when there is no upper RR cap."""
    decision = {
        "order_type": "限价单",
        "order_direction": "做多",
        "entry_price": 100.0,
        "take_profit_price": 110.0,
        "take_profit_price_2": 115.0,
        "stop_loss_price": 95.0,
        "estimated_win_rate": 55,
    }
    errors = validate_order_trade_metrics(decision, decision_stance="aggressive")
    assert not errors
    rr = compute_risk_reward(100.0, 110.0, 95.0, "做多")
    assert rr is not None
    assert rr["ratio"] == 2.0


def test_stage2_validator_coerces_bad_rr_to_no_order() -> None:
    obj = {
        "decision": {
            "order_type": "突破单",
            "order_direction": "做多",
            "entry_price": 4527.4,
            "take_profit_price": 4529.5,
            "take_profit_price_2": 4532.0,
            "stop_loss_price": 4524.9,
            "reasoning": "test",
            "diagnosis_confidence": 60,
            "diagnosis_confidence_reasoning": "test",
            "trade_confidence": 50,
            "trade_confidence_reasoning": "test",
            "estimated_win_rate": 47,
            "estimated_win_rate_reasoning": "test",
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "test",
            "invalidation_condition": "test",
            "entry_basis_bar": "K2",
            "entry_basis_extreme": "high",
            "entry_rule": "K2高点上方1跳动",
        },
        "diagnosis_summary": {
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "key_signals": [],
        },
        "bar_analysis": {
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
            "second_entry": {"is_second_entry": False, "type": "none"},
        },
        "decision_trace": [
            {
                "node_id": "10.3",
                "question": "交易者方程是否通过？",
                "answer": "是",
                "reason": "wrong",
                "bar_range": "K1",
            },
            {
                "node_id": "11.1",
                "question": "趋势？",
                "answer": "是",
                "reason": "test",
                "bar_range": "K1",
            },
        ],
        "terminal": {"node_id": "11.1", "outcome": "trade", "label": "test"},
    }
    result = validator.validate(
        "stage2", json.dumps(obj), decision_stance="aggressive"
    )
    assert isinstance(result, Ok)
    assert result.obj["decision"]["order_type"] == "不下单"
    assert result.obj["decision_trace"][0]["answer"] == "否"
    assert result.obj["terminal"]["outcome"] == "reject"


def test_stage2_validator_auto_fixes_breakout_entry_at_or_inside_basis_high() -> None:
    """Entry at/below K2 high is bumped to high + 1 tick before breakout_price check."""
    obj = _stage2_trade_obj(entry_price=101.5, take_profit_price=106.5, stop_loss_price=100.0)
    result = validator.validate(
        "stage2",
        json.dumps(obj),
        decision_stance="aggressive",
        kline_frame=_frame(),
    )
    assert isinstance(result, Ok)
    entry = result.obj["decision"]["entry_price"]
    assert entry > 102.0


def test_stage2_validator_normalizes_stale_entry_bar_to_pending() -> None:
    """Lenient mode treats stale pending-entry variants as pending, not hard-fail."""
    obj = _stage2_trade_obj()
    obj["bar_analysis"]["entry_bar"]["freshness"] = "stale"
    result = validator.validate(
        "stage2",
        json.dumps(obj),
        decision_stance="aggressive",
        kline_frame=_frame(),
    )
    assert isinstance(result, Ok)
    assert result.obj["bar_analysis"]["entry_bar"]["freshness"] == "pending"


def test_stage2_validator_accepts_pending_limit_entry_bar() -> None:
    obj = _stage2_trade_obj(
        order_type="限价单",
        order_direction="做空",
        entry_price=101.0,
        take_profit_price=98.0,
        stop_loss_price=103.0,
        trade_confidence=65,
        estimated_win_rate=60,
        entry_basis_bar=None,
        entry_basis_extreme=None,
        entry_rule="等待价格反弹到阻力位后挂限价卖单",
    )
    obj["bar_analysis"]["always_in"] = "short"
    obj["bar_analysis"]["signal_bar"]["bar"] = "K2"
    obj["bar_analysis"]["signal_bar"]["pattern"] = "L1"
    obj["bar_analysis"]["entry_bar"] = {
        "bar": None,
        "strength": "not_triggered",
        "follow_through": False,
        "still_valid": True,
        "freshness": "pending",
    }
    result = validator.validate(
        "stage2",
        json.dumps(obj),
        decision_stance="aggressive",
        kline_frame=_frame(),
    )
    assert isinstance(result, Ok), f"Expected Ok, got {result}"


def test_stage2_validator_accepts_planned_limit_without_signal_bar() -> None:
    obj = _stage2_trade_obj(
        order_type="限价单",
        order_direction="做空",
        entry_price=101.0,
        take_profit_price=98.0,
        stop_loss_price=103.0,
        trade_confidence=50,
        trade_confidence_reasoning="极度激进档接受无信号棒瑕疵",
        estimated_win_rate=55,
        entry_basis_bar=None,
        entry_basis_extreme=None,
        entry_rule=None,
    )
    obj["bar_analysis"]["always_in"] = "short"
    obj["bar_analysis"]["signal_bar"] = {
        "bar": None,
        "quality": "invalid",
        "pattern": "none",
        "reason": "计划型限价单，尚无已收盘信号棒",
    }
    obj["bar_analysis"]["entry_bar"] = {
        "bar": None,
        "strength": "not_triggered",
        "follow_through": False,
        "still_valid": True,
        "freshness": "pending",
    }
    obj["decision_trace"] = [
        {
            "node_id": "9.0",
            "question": "信号棒是否已经收盘且质量足够？",
            "answer": "否",
            "reason": "无合格信号棒，改走背景限价路径",
            "skipped": False,
            "bar_range": "K3-K1",
        },
        {
            "node_id": "9.0P",
            "question": "背景驱动限价单评估（§9.0=否 时必须评估）",
            "answer": "是",
            "reason": "计划型限价挂阻力区，周期/边界支持",
            "skipped": False,
            "bar_range": "K10-K1",
        },
        *obj["decision_trace"],
    ]
    result = validator.validate(
        "stage2",
        json.dumps(obj),
        decision_stance="balanced",
        kline_frame=_frame(),
    )
    assert isinstance(result, Ok), f"Expected Ok, got {result}"


def test_stage2_validator_accepts_planned_limit_invalid_tr_boundary_null() -> None:
    """§9.0P zone-boundary limit: invalid + tr_boundary + bar=null must not retry."""
    obj = _stage2_trade_obj(
        order_type="限价单",
        order_direction="做空",
        entry_price=101.0,
        take_profit_price=98.0,
        stop_loss_price=103.0,
        trade_confidence=50,
        trade_confidence_reasoning="极度激进档接受无信号棒瑕疵",
        estimated_win_rate=55,
        entry_basis_bar=None,
        entry_basis_extreme=None,
        entry_rule="区间上边界反弹挂空",
    )
    obj["bar_analysis"]["always_in"] = "short"
    obj["bar_analysis"]["signal_bar"] = {
        "bar": None,
        "quality": "invalid",
        "pattern": "tr_boundary",
        "reason": "计划型限价单，尚无已收盘信号棒，边界 setup",
    }
    obj["bar_analysis"]["entry_bar"] = {
        "bar": None,
        "strength": "not_triggered",
        "follow_through": "pending",
        "still_valid": True,
        "freshness": "pending",
    }
    obj["decision_trace"] = [
        {
            "node_id": "9.0",
            "question": "信号棒是否已经收盘且质量足够？",
            "answer": "否",
            "reason": "无合格信号棒，改走背景限价路径",
            "skipped": False,
            "bar_range": "K3-K1",
        },
        {
            "node_id": "9.0P",
            "question": "背景驱动限价单评估（§9.0=否 时必须评估）",
            "answer": "是",
            "reason": "计划型限价挂阻力区，周期/边界支持",
            "skipped": False,
            "bar_range": "K10-K1",
        },
        *obj["decision_trace"],
    ]
    result = validator.validate(
        "stage2",
        json.dumps(obj),
        decision_stance="balanced",
        kline_frame=_frame(),
    )
    assert isinstance(result, Ok), f"Expected Ok, got {result}"


def test_stage2_validator_accepts_planned_limit_with_weak_signal_bar() -> None:
    obj = _stage2_trade_obj(
        order_type="限价单",
        order_direction="做空",
        entry_price=101.0,
        take_profit_price=98.0,
        stop_loss_price=103.0,
        trade_confidence=55,
        estimated_win_rate=52,
        entry_basis_bar=None,
        entry_basis_extreme=None,
        entry_rule="区间上边界反弹挂空",
    )
    obj["bar_analysis"]["always_in"] = "short"
    obj["bar_analysis"]["signal_bar"] = {
        "bar": "K2",
        "quality": "weak",
        "pattern": "tr_boundary",
        "reason": "边界弱反弹棒，计划型限价接受次优信号",
    }
    obj["bar_analysis"]["entry_bar"] = {
        "bar": None,
        "strength": "not_triggered",
        "follow_through": "pending",
        "still_valid": True,
        "freshness": "pending",
    }
    obj["decision_trace"] = [
        {
            "node_id": "9.0",
            "question": "信号棒是否已经收盘且质量足够？",
            "answer": "是",
            "reason": "计划型限价：宽通道上边界 weak 信号可接受，等待反弹到位",
            "skipped": False,
            "bar_range": "K3-K1",
        },
        *obj["decision_trace"],
    ]
    result = validator.validate(
        "stage2",
        json.dumps(obj),
        decision_stance="balanced",
        kline_frame=_frame(),
    )
    assert isinstance(result, Ok), f"Expected Ok, got {result}"


def test_stage2_validator_rejects_strong_signal_without_signal_bar() -> None:
    obj = _stage2_trade_obj()
    obj["bar_analysis"]["signal_bar"]["bar"] = None
    obj["bar_analysis"]["signal_bar"]["quality"] = "strong"
    obj["bar_analysis"]["entry_bar"] = {
        "bar": None,
        "strength": "not_triggered",
        "follow_through": "pending",
        "still_valid": True,
        "freshness": "pending",
    }
    result = validator.validate(
        "stage2",
        json.dumps(obj),
        decision_stance="aggressive",
        kline_frame=_frame(),
    )
    assert isinstance(result, ValidationError)
    assert any("signal_bar.bar" in f for f in result.invalid_fields)


def test_stage2_validator_auto_fixes_pending_market_entry_bar() -> None:
    obj = _stage2_trade_obj(
        order_type="市价单",
        entry_price=102.1,
        take_profit_price=105.0,
        stop_loss_price=100.0,
        entry_basis_bar=None,
        entry_basis_extreme=None,
    )
    obj["bar_analysis"]["entry_bar"] = {
        "bar": None,
        "strength": "not_triggered",
        "follow_through": "pending",
        "still_valid": True,
        "freshness": "pending",
    }
    result = validator.validate(
        "stage2",
        json.dumps(obj),
        decision_stance="aggressive",
        kline_frame=_frame(),
    )
    assert isinstance(result, Ok)
    assert result.obj["bar_analysis"]["entry_bar"]["bar"] == "K1"


def test_stage2_validator_accepts_grounded_trade() -> None:
    obj = _stage2_trade_obj()
    result = validator.validate(
        "stage2",
        json.dumps(obj),
        decision_stance="aggressive",
        kline_frame=_frame(),
    )
    assert isinstance(result, Ok), f"Expected Ok, got {result}"


def test_planned_limit_allows_k1_wick_touch_entry() -> None:
    """Pending sell limit: K1 wick may exceed entry; only close side matters."""
    from pa_agent.util.trade_metrics import validate_limit_order_k1_freshness

    decision = {
        "order_type": "限价单",
        "order_direction": "做空",
        "entry_price": 103.5,
        "stop_loss_price": 106.0,
        "take_profit_price": 101.0,
    }
    bar_analysis = {
        "entry_bar": {
            "bar": None,
            "strength": "not_triggered",
            "freshness": "pending",
        }
    }
    errors = validate_limit_order_k1_freshness(
        decision, _frame(), bar_analysis=bar_analysis
    )
    assert not errors, errors
