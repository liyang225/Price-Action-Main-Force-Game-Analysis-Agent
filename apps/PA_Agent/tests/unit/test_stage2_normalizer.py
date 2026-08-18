"""Unit tests for Stage 2 normalizer — next_bar_prediction (T4)."""
from __future__ import annotations

import json

from pa_agent.ai.json_validator import Ok
from pa_agent.ai.stage2_normalizer import (
    _normalize_closed_enum,
    _normalize_stage2_bar_analysis_enums,
    _strip_enum_suffix,
    normalize_stage2,
    _normalize_next_bar_prediction,
)
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from tests.fixtures.validators import schema_test_validator


# ── Closed enum annotation stripping (bar_type / freshness) ────────────────


def test_strip_enum_suffix_removes_chinese_parenthetical() -> None:
    assert _strip_enum_suffix("invalid（K1突破K2 low后反向）") == "invalid"
    assert _strip_enum_suffix("outside_bull（沿用阶段一bar_analysis.bar_type）") == "outside_bull"


def test_normalize_closed_enum_freshness_with_annotation() -> None:
    assert _normalize_closed_enum(
        "invalid（信号失效）",
        frozenset({"fresh", "pending", "stale", "invalid"}),
    ) == "invalid"


def test_normalize_stage2_bar_analysis_enums_from_user_report() -> None:
    out = {
        "bar_analysis": {
            "always_in": "long",
            "last_closed_bar": "K1",
            "bar_type": "outside_bull（沿用阶段一bar_analysis.bar_type）",
            "entry_bar": {
                "strength": "weak",
                "follow_through": False,
                "still_valid": False,
                "freshness": "invalid（K1突破K2 low=64198.94触发做空入场后收阳线反向）",
            },
        }
    }
    stage1 = {"bar_analysis": {"bar_type": "outside_bull"}}
    assert _normalize_stage2_bar_analysis_enums(out, stage1_json=stage1) is True
    assert out["bar_analysis"]["bar_type"] == "outside_bull"
    assert out["bar_analysis"]["entry_bar"]["freshness"] == "invalid"


def test_normalize_second_entry_type_null_passes_schema() -> None:
    """Models emit type=null when is_second_entry=false; schema requires string."""
    payload = {
        "decision": {
            "order_type": "不下单",
            "order_direction": None,
            "entry_price": None,
            "take_profit_price": None,
            "stop_loss_price": None,
            "reasoning": "区间中部等待",
            "diagnosis_confidence": 65,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 55,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": None,
            "estimated_win_rate_reasoning": None,
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
            "invalidation_condition": "",
        },
        "diagnosis_summary": {
            "cycle_position": "trending_tr",
            "direction": "neutral",
            "key_signals": [],
        },
        "bar_analysis": {
            "always_in": "long",
            "last_closed_bar": "K1",
            "bar_type": "other",
            "signal_bar": {
                "bar": "K1",
                "quality": "weak",
                "pattern": "none",
                "reason": "弱信号",
            },
            "entry_bar": {
                "strength": "not_triggered",
                "follow_through": False,
                "still_valid": False,
                "freshness": "invalid",
            },
            "second_entry": {"is_second_entry": False, "type": None},
        },
        "decision_trace": [
            {
                "node_id": "9.0",
                "question": "信号棒是否合格？",
                "answer": "否",
                "reason": "区间中部",
                "bar_range": "K8-K1",
            },
        ],
        "terminal": {
            "node_id": "9.0",
            "outcome": "wait",
            "label": "区间中部等待边界触发",
        },
    }
    out = normalize_stage2(payload)
    assert out["bar_analysis"]["second_entry"]["type"] == "none"

    result = schema_test_validator().validate(
        "stage2",
        json.dumps(out, ensure_ascii=False),
    )
    assert isinstance(result, Ok)


def test_normalize_stage2_enum_annotations_passes_schema() -> None:
    payload = {
        "decision": {
            "order_type": "不下单",
            "order_direction": None,
            "entry_price": None,
            "take_profit_price": None,
            "stop_loss_price": None,
            "reasoning": "信号失效，等待",
            "diagnosis_confidence": 70,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 65,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": None,
            "estimated_win_rate_reasoning": None,
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
            "invalidation_condition": "",
        },
        "diagnosis_summary": {
            "cycle_position": "broad_channel",
            "direction": "bearish",
            "key_signals": [],
        },
        "bar_analysis": {
            "always_in": "long",
            "last_closed_bar": "K1",
            "bar_type": "outside_bull（沿用阶段一bar_analysis.bar_type）",
            "signal_bar": {
                "bar": "K2",
                "quality": "strong",
                "pattern": "none",
                "reason": "test",
            },
            "entry_bar": {
                "bar": "K1",
                "strength": "weak",
                "follow_through": False,
                "still_valid": False,
                "freshness": "invalid（K1突破K2 low后反向，信号失效）",
            },
        },
        "decision_trace": [
            {
                "node_id": "9.0",
                "question": "信号棒是否合格？",
                "answer": "否",
                "reason": "freshness=invalid",
                "bar_range": "K2-K1",
            },
        ],
        "terminal": {
            "node_id": "9.0",
            "outcome": "wait",
            "label": "无可靠入场方案",
        },
    }
    stage1 = {"bar_analysis": {"bar_type": "outside_bull"}}
    out = normalize_stage2(payload, stage1_json=stage1)
    assert out["bar_analysis"]["bar_type"] == "outside_bull"
    assert out["bar_analysis"]["entry_bar"]["freshness"] == "invalid"

    result = schema_test_validator().validate(
        "stage2",
        json.dumps(out, ensure_ascii=False),
        stage1_json=stage1,
    )
    assert isinstance(result, Ok)


# ── _normalize_next_bar_prediction direct tests ──────────────────────────────


def test_normalize_next_bar_prediction_unpredictable_forces_null():
    """unpredictable=true → direction/probabilities normalized to None."""
    pred = {
        "direction": "bullish",
        "probabilities": {"bullish": 60, "bearish": 30, "neutral": 10},
        "reasoning": "test",
        "unpredictable": True,
        "features_used": ["stage1_diagnosis"],
    }
    _normalize_next_bar_prediction(pred)
    assert pred["unpredictable"] is True
    assert pred["direction"] is None
    assert pred["probabilities"] is None


def test_normalize_next_bar_prediction_rounds_probabilities():
    """Float probabilities must be rounded to ints, clamped to [0, 100]."""
    pred = {
        "direction": "bullish",
        "probabilities": {"bullish": 49.7, "bearish": 30.3, "neutral": 20.0},
        "reasoning": "test",
        "unpredictable": False,
        "features_used": ["stage1_diagnosis"],
    }
    _normalize_next_bar_prediction(pred)
    probs = pred["probabilities"]
    assert probs == {"bullish": 50, "bearish": 30, "neutral": 20}


def test_normalize_next_bar_prediction_direction_argmax():
    """direction must be corrected to argmax of probabilities."""
    pred = {
        "direction": "bearish",  # wrong
        "probabilities": {"bullish": 55, "bearish": 35, "neutral": 10},
        "reasoning": "test",
        "unpredictable": False,
        "features_used": ["stage1_diagnosis"],
    }
    _normalize_next_bar_prediction(pred)
    assert pred["direction"] == "bullish"


def test_normalize_next_bar_prediction_direction_argmax_tie_break():
    """Tied probabilities: break by literal order (bullish > bearish > neutral)."""
    pred = {
        "direction": "neutral",
        "probabilities": {"bullish": 40, "bearish": 40, "neutral": 20},
        "reasoning": "test",
        "unpredictable": False,
        "features_used": ["stage1_diagnosis"],
    }
    _normalize_next_bar_prediction(pred)
    assert pred["direction"] == "bullish"  # bullish before bearish


def test_normalize_next_bar_prediction_features_used_dedup_min():
    """features_used must be deduplicated and contain at least stage1_diagnosis."""
    pred = {
        "direction": "bullish",
        "probabilities": {"bullish": 70, "bearish": 20, "neutral": 10},
        "reasoning": "test",
        "unpredictable": False,
        "features_used": ["kline_features", "kline_features", "stage1_diagnosis"],
    }
    _normalize_next_bar_prediction(pred)
    assert pred["features_used"] == ["kline_features", "stage1_diagnosis"]


def test_normalize_next_bar_prediction_features_used_min_set():
    """Missing stage1_diagnosis gets prepended."""
    pred = {
        "direction": "bullish",
        "probabilities": {"bullish": 70, "bearish": 20, "neutral": 10},
        "reasoning": "test",
        "unpredictable": False,
        "features_used": ["kline_features"],
    }
    _normalize_next_bar_prediction(pred)
    assert pred["features_used"][0] == "stage1_diagnosis"


def test_normalize_decision_reasoning_truncation() -> None:
    """decision.reasoning > 280 chars gets truncated."""
    from pa_agent.ai.stage2_normalizer import DECISION_REASONING_MAX_LEN, normalize_stage2

    obj = {
        "decision": {
            "order_type": "不下单",
            "order_direction": None,
            "entry_price": None,
            "take_profit_price": None,
            "stop_loss_price": None,
            "reasoning": "长" * 400,
            "diagnosis_confidence": 60,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 40,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": None,
            "estimated_win_rate_reasoning": None,
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
        },
        "decision_trace": [],
        "terminal": {"node_id": "10.2", "outcome": "wait", "label": "wait"},
    }
    out = normalize_stage2(obj)
    assert len(out["decision"]["reasoning"]) == DECISION_REASONING_MAX_LEN
    assert out["decision"]["reasoning"].endswith("…")


def test_normalize_stage2_retruncates_after_continuity_guard() -> None:
    from unittest.mock import patch

    from pa_agent.ai.stage2_normalizer import DECISION_REASONING_MAX_LEN, normalize_stage2

    guarded = {
        "decision": {
            "order_type": "不下单",
            "order_direction": None,
            "entry_price": None,
            "stop_loss_price": None,
            "take_profit_price": None,
            "reasoning": "【程序连续性守卫】" + "x" * 400,
            "diagnosis_confidence": 60,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 0,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": None,
            "estimated_win_rate_reasoning": None,
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
        },
        "decision_trace": [],
        "terminal": {"node_id": "continuity", "outcome": "wait", "label": "方案连续性守卫"},
    }

    def _fake_guard(stage2, ctx):
        return guarded

    obj = dict(guarded)
    with patch("pa_agent.ai.decision_continuity.apply_continuity_guard", side_effect=_fake_guard):
        out = normalize_stage2(obj, kline_frame=object(), stage1_json={"direction": "neutral"})
    assert len(out["decision"]["reasoning"]) <= DECISION_REASONING_MAX_LEN


def test_normalize_next_bar_prediction_reasoning_truncation():
    """Reasoning > 1500 chars gets truncated with ellipsis."""
    pred = {
        "direction": "bullish",
        "probabilities": {"bullish": 70, "bearish": 20, "neutral": 10},
        "reasoning": "x" * 2000,
        "unpredictable": False,
        "features_used": ["stage1_diagnosis"],
    }
    _normalize_next_bar_prediction(pred)
    assert len(pred["reasoning"]) == 1500
    assert pred["reasoning"].endswith("…")


def test_normalize_next_bar_prediction_non_string_reasoning():
    """Non-string reasoning becomes empty string."""
    pred = {
        "direction": "bullish",
        "probabilities": {"bullish": 70, "bearish": 20, "neutral": 10},
        "reasoning": 42,
        "unpredictable": False,
        "features_used": ["stage1_diagnosis"],
    }
    _normalize_next_bar_prediction(pred)
    assert pred["reasoning"] == ""


def test_normalize_next_bar_prediction_idempotent():
    """Calling normalize twice must produce same result."""
    pred = {
        "direction": "bearish",
        "probabilities": {"bullish": 55, "bearish": 35, "neutral": 10},
        "reasoning": "test reasoning for idempotency check",
        "unpredictable": False,
        "features_used": ["stage1_diagnosis"],
    }
    _normalize_next_bar_prediction(pred)
    first = {**pred}
    _normalize_next_bar_prediction(pred)
    assert pred == first


# ── Integration: normalize_stage2 with prediction ────────────────────────────


def test_normalize_stage2_with_prediction():
    """normalize_stage2 must call _normalize_next_bar_prediction."""
    obj = {
        "decision": {
            "order_type": "不下单",
            "order_direction": None,
            "entry_price": None,
            "take_profit_price": None,
            "stop_loss_price": None,
            "reasoning": "test",
            "diagnosis_confidence": 40,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 30,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": 55,
            "estimated_win_rate_reasoning": "t",
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
            "invalidation_condition": "t",
        },
        "diagnosis_summary": {
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "key_signals": [],
        },
        "decision_trace": [
            {"node_id": "10.3", "question": "q", "answer": "否", "reason": "r", "bar_range": "K1"},
        ],
        "terminal": {"node_id": "10.3", "outcome": "wait", "label": "test"},
        "next_bar_prediction": {
            "direction": "bearish",  # wrong: argmax is bullish
            "probabilities": {"bullish": 55.4, "bearish": 34.6, "neutral": 10.0},
            "reasoning": "test",
            "unpredictable": False,
            "features_used": [],
        },
    }
    result = normalize_stage2(obj)
    pred = result["next_bar_prediction"]
    assert pred["direction"] == "bullish"
    assert pred["probabilities"] == {"bullish": 55, "bearish": 35, "neutral": 10}
    assert pred["features_used"] == ["stage1_diagnosis"]


def test_coerce_no_order_when_metrics_fail_after_breakout_entry_snap() -> None:
    """Regression: wrong breakout entry snapped → RR/equation fail → 不下单."""
    frame = KlineFrame(
        symbol="XAUUSD",
        timeframe="1h",
        bars=(
            KlineBar(
                seq=1,
                ts_open=1.0,
                open=10.80,
                high=10.83,
                low=10.66,
                close=10.72,
                volume=1,
                closed=True,
            ),
            KlineBar(
                seq=2,
                ts_open=0.0,
                open=10.71,
                high=10.71,
                low=10.67,
                close=10.68,
                volume=1,
                closed=True,
            ),
        ),
        indicators=IndicatorBundle(ema20=(10.86, 10.86), atr14=(0.08, 0.08)),
        snapshot_ts_local_ms=1,
    )
    payload = {
        "decision": {
            "order_direction": "做空",
            "order_type": "突破单",
            "entry_price": 10.72,
            "entry_basis_bar": "K2",
            "entry_basis_extreme": "low",
            "entry_rule": "K2 low - 1 tick",
            "take_profit_price": 10.50,
            "take_profit_price_2": 10.30,
            "stop_loss_price": 10.84,
            "reasoning": "test",
            "diagnosis_confidence": 72,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 65,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": 50,
            "estimated_win_rate_reasoning": "t",
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
            "invalidation_condition": "t",
        },
        "diagnosis_summary": {
            "cycle_position": "broad_channel",
            "direction": "bearish",
            "key_signals": [],
        },
        "decision_trace": [
            {
                "node_id": "10.3",
                "question": "交易者方程是否通过？",
                "answer": "是",
                "reason": "用10.72/10.84/10.50算RR约1.8",
                "bar_range": "K2-K1",
            },
            {
                "node_id": "11.2",
                "question": "通道回撤？",
                "answer": "是",
                "reason": "test",
                "bar_range": "K2-K1",
            },
        ],
        "terminal": {"node_id": "11.2", "outcome": "trade", "label": "突破做空"},
    }
    out = normalize_stage2(
        payload,
        kline_frame=frame,
        decision_stance="extreme_aggressive",
    )
    assert out["decision"]["order_type"] == "不下单"
    assert out["decision"]["entry_price"] is None
    # Node 10.3 should have answer=否 (now may not be first after §9 node injection)
    trace_103 = next((n for n in out["decision_trace"] if n.get("node_id") == "10.3"), None)
    assert trace_103 is not None, "node 10.3 should be in decision_trace"
    assert trace_103["answer"] == "否"
    assert out["terminal"]["node_id"] == "10.3"
    assert out["terminal"]["outcome"] == "reject"

    result = schema_test_validator().validate(
        "stage2",
        json.dumps(out, ensure_ascii=False),
        decision_stance="extreme_aggressive",
        kline_frame=frame,
    )
    assert isinstance(result, Ok)


def test_coerce_decision_when_103_no_but_prices_remain() -> None:
    """Regression: model says 10.3=否 / terminal=reject but leaves 突破单 prices."""
    payload = {
        "decision": {
            "order_direction": "做多",
            "order_type": "突破单",
            "entry_price": 10.88,
            "entry_basis_bar": "K1",
            "entry_basis_extreme": "high",
            "entry_rule": "K1 高点上方 1 跳动",
            "take_profit_price": 10.94,
            "take_profit_price_2": 11.00,
            "stop_loss_price": 10.81,
            "reasoning": "方程不通过但仍写突破单",
            "diagnosis_confidence": 58,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 30,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": 45,
            "estimated_win_rate_reasoning": "t",
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
            "invalidation_condition": "t",
        },
        "diagnosis_summary": {
            "cycle_position": "trading_range",
            "direction": "neutral",
            "key_signals": [],
        },
        "decision_trace": [
            {
                "node_id": "10.3",
                "question": "交易者方程是否通过？",
                "answer": "否",
                "reason": "RR 0.86:1，45% 胜率方程不通过",
                "bar_range": "K1",
            },
            {
                "node_id": "14.0",
                "question": "是否违反禁止行为清单？",
                "answer": "是",
                "reason": "方程不通过仍强行交易",
                "bar_range": "不适用",
            },
        ],
        "terminal": {
            "node_id": "14.0",
            "outcome": "reject",
            "label": "禁止行为",
        },
    }
    out = normalize_stage2(payload)
    d = out["decision"]
    assert d["order_type"] == "不下单"
    assert d["entry_price"] is None
    assert d["estimated_win_rate"] is None
    assert out["terminal"]["node_id"] == "10.3"

    result = schema_test_validator().validate("stage2", json.dumps(out, ensure_ascii=False))
    assert isinstance(result, Ok)


def test_trade_terminal_14_repaired_to_order_node() -> None:
    """§14 is a prohibition scan, not the terminal node for successful trades."""
    payload = {
        "decision": {
            "order_direction": "做空",
            "order_type": "限价单",
            "entry_price": 100.0,
            "entry_basis_bar": None,
            "entry_basis_extreme": None,
            "entry_rule": None,
            "take_profit_price": 90.0,
            "take_profit_price_2": 84.0,
            "stop_loss_price": 107.0,
            "reasoning": "test",
            "diagnosis_confidence": 70,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 55,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": 55,
            "estimated_win_rate_reasoning": "t",
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
            "invalidation_condition": "t",
        },
        "diagnosis_summary": {
            "cycle_position": "trading_range",
            "direction": "bearish",
            "key_signals": [],
        },
        "bar_analysis": {
            "always_in": "short",
            "last_closed_bar": "K1",
            "bar_type": "doji",
            "signal_bar": {
                "bar": None,
                "quality": "invalid",
                "pattern": "none",
                "reason": "计划型限价",
            },
            "entry_bar": {
                "bar": None,
                "strength": "not_triggered",
                "follow_through": "pending",
                "freshness": "pending",
            },
        },
        "decision_trace": [
            {
                "node_id": "9.0",
                "question": "信号棒是否合格？",
                "answer": "是",
                "reason": "计划型限价",
                "bar_range": "K1",
            },
            {
                "node_id": "10.1",
                "question": "是否能明确止损？",
                "answer": "是",
                "reason": "t",
                "bar_range": "K1",
            },
            {
                "node_id": "10.2",
                "question": "止损是否过大？",
                "answer": "否",
                "reason": "t",
                "bar_range": "K1",
            },
            {
                "node_id": "10.3",
                "question": "交易者方程是否通过？",
                "answer": "是",
                "reason": "risk=7 reward=10",
                "bar_range": "K1",
            },
            {
                "node_id": "11.3",
                "question": "是交易区间吗？",
                "answer": "是",
                "reason": "限价单",
                "bar_range": "K1",
            },
            {
                "node_id": "14.1",
                "question": "是否触犯禁止行为？",
                "answer": "否",
                "reason": "未触犯任何禁止项",
                "bar_range": "K1",
            },
        ],
        "terminal": {"node_id": "14.1", "outcome": "trade", "label": "限价做空"},
    }
    out = normalize_stage2(payload)
    assert out["terminal"]["node_id"] == "11.3"
    assert out["terminal"]["outcome"] == "trade"
    assert out["decision"]["order_type"] == "限价单"

    result = schema_test_validator().validate("stage2", json.dumps(out, ensure_ascii=False))
    assert isinstance(result, Ok)


def test_signal_bar_bumped_when_same_seq_as_entry() -> None:
    obj = {
        "decision": {
            "order_type": "突破单",
            "order_direction": "做空",
            "entry_price": 3.42,
            "entry_basis_bar": "K3",
            "entry_basis_extreme": "low",
            "entry_rule": "test",
            "take_profit_price": 3.20,
            "take_profit_price_2": 3.00,
            "stop_loss_price": 3.6,
            "reasoning": "t",
            "diagnosis_confidence": 50,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 50,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": 50,
            "estimated_win_rate_reasoning": "t",
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
            "invalidation_condition": "t",
        },
        "bar_analysis": {
            "signal_bar": {"bar": "K1", "quality": "valid", "pattern": "bear_reversal"},
            "entry_bar": {
                "bar": "K1",
                "strength": "strong",
                "freshness": "fresh",
                "follow_through": "good",
            },
        },
        "diagnosis_summary": {
            "cycle_position": "trading_range",
            "direction": "bearish",
            "key_signals": [],
        },
        "decision_trace": [],
        "terminal": {"node_id": "0", "outcome": "trade", "label": "t"},
    }
    out = normalize_stage2(obj)
    assert out["bar_analysis"]["signal_bar"]["bar"] == "K2"


def test_normalize_stage2_without_prediction_noop():
    """Legacy Stage 2 without prediction must normalize without error."""
    obj = {
        "decision": {
            "order_type": "不下单",
            "order_direction": None,
            "entry_price": None,
            "take_profit_price": None,
            "stop_loss_price": None,
            "reasoning": "test",
            "diagnosis_confidence": 40,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 30,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": None,
            "estimated_win_rate_reasoning": "t",
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
            "invalidation_condition": "t",
        },
        "diagnosis_summary": {
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "key_signals": [],
        },
        "decision_trace": [],
        "terminal": {"node_id": "0", "outcome": "wait", "label": "test"},
    }
    result = normalize_stage2(obj)
    assert isinstance(result.get("next_bar_prediction"), dict)
    assert isinstance(result.get("next_cycle_prediction"), dict)


def test_repair_next_bar_yinxian_singular_probability() -> None:
    """阴线 + probability shorthand from production failures must normalize."""
    obj = {
        "decision": {
            "order_type": "不下单",
            "order_direction": None,
            "entry_price": None,
            "take_profit_price": None,
            "stop_loss_price": None,
            "reasoning": "test",
            "diagnosis_confidence": 55,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 55,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": None,
            "estimated_win_rate_reasoning": None,
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
            "invalidation_condition": "t",
        },
        "diagnosis_summary": {
            "cycle_position": "trending_tr",
            "direction": "neutral",
            "key_signals": [],
        },
        "decision_trace": [],
        "terminal": {"node_id": "0", "outcome": "wait", "label": "test"},
        "next_bar_prediction": {
            "direction": "阴线",
            "probability": 60,
            "reasoning": "测试理由" * 10,
        },
        "next_cycle_prediction": {
            "cycle": "trending_tr",
            "direction": "neutral",
            "probabilities": {
                "spike": 3,
                "micro_channel": 5,
                "tight_channel": 8,
                "normal_channel": 20,
                "broad_channel": 35,
                "trending_tr": 15,
                "trading_range": 10,
                "extreme_tr": 4,
            },
            "unpredictable": False,
            "reasoning": "周期预测理由",
            "features_used": ["stage1_diagnosis"],
        },
    }
    out = normalize_stage2(obj)
    nb = out["next_bar_prediction"]
    assert nb["direction"] == "bearish"
    assert isinstance(nb["probabilities"], dict)
    assert sum(nb["probabilities"].values()) == 100
    assert nb["probabilities"]["bearish"] == 60


def test_prediction_guard_forbids_long_when_next_cycle_bearish() -> None:
    obj = {
        "decision": {
            "order_type": "限价单",
            "order_direction": "做多",
            "entry_price": 4015.365,
            "take_profit_price": 4022.486,
            "take_profit_price_2": 4034.234,
            "stop_loss_price": 4009.472,
            "reasoning": "test",
            "diagnosis_confidence": 64,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 52,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": 52,
            "estimated_win_rate_reasoning": "t",
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
            "invalidation_condition": "t",
        },
        "diagnosis_summary": {
            "cycle_position": "trending_tr",
            "direction": "neutral",
            "key_signals": [],
        },
        "decision_trace": [],
        "terminal": {"node_id": "11.3", "outcome": "trade", "label": "t"},
        "next_cycle_prediction": {
            "cycle": "broad_channel",
            "direction": "bearish",
            "probabilities": {
                "spike": 5,
                "micro_channel": 5,
                "tight_channel": 8,
                "normal_channel": 12,
                "broad_channel": 30,
                "trending_tr": 20,
                "trading_range": 15,
                "extreme_tr": 5,
            },
            "unpredictable": False,
            "reasoning": "x",
            "features_used": ["stage1_diagnosis"],
        },
    }
    out = normalize_stage2(obj)
    assert out["decision"]["order_type"] == "不下单"
    assert out["terminal"]["outcome"] == "wait"
    assert "禁止做多" in (out["decision"].get("reasoning") or "")


def test_prediction_guard_forbids_short_when_next_cycle_bullish() -> None:
    obj = {
        "decision": {
            "order_type": "限价单",
            "order_direction": "做空",
            "entry_price": 4022.486,
            "take_profit_price": 4015.365,
            "take_profit_price_2": 4009.473,
            "stop_loss_price": 4034.234,
            "reasoning": "test",
            "diagnosis_confidence": 64,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 52,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": 52,
            "estimated_win_rate_reasoning": "t",
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
            "invalidation_condition": "t",
        },
        "diagnosis_summary": {
            "cycle_position": "trending_tr",
            "direction": "neutral",
            "key_signals": [],
        },
        "decision_trace": [],
        "terminal": {"node_id": "11.3", "outcome": "trade", "label": "t"},
        "next_cycle_prediction": {
            "cycle": "broad_channel",
            "direction": "bullish",
            "probabilities": {
                "spike": 5,
                "micro_channel": 5,
                "tight_channel": 8,
                "normal_channel": 12,
                "broad_channel": 30,
                "trending_tr": 20,
                "trading_range": 15,
                "extreme_tr": 5,
            },
            "unpredictable": False,
            "reasoning": "x",
            "features_used": ["stage1_diagnosis"],
        },
    }
    out = normalize_stage2(obj)
    assert out["decision"]["order_type"] == "不下单"
    assert out["terminal"]["outcome"] == "wait"
    assert "禁止做空" in (out["decision"].get("reasoning") or "")


def test_validator_injects_next_bar_when_feature_disabled() -> None:
    """skip_next_bar=True must not skip schema-required injection during validate()."""
    payload = {
        "decision": {
            "order_type": "不下单",
            "order_direction": None,
            "entry_price": None,
            "take_profit_price": None,
            "stop_loss_price": None,
            "reasoning": "test",
            "diagnosis_confidence": 55,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 55,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": None,
            "estimated_win_rate_reasoning": None,
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
            "invalidation_condition": "t",
        },
        "diagnosis_summary": {
            "cycle_position": "trending_tr",
            "direction": "neutral",
            "key_signals": [],
        },
        "decision_trace": [],
        "terminal": {"node_id": "0", "outcome": "wait", "label": "test"},
        "next_cycle_prediction": {
            "cycle": "trending_tr",
            "direction": "neutral",
            "probabilities": {
                "spike": 3,
                "micro_channel": 5,
                "tight_channel": 8,
                "normal_channel": 20,
                "broad_channel": 35,
                "trending_tr": 15,
                "trading_range": 10,
                "extreme_tr": 4,
            },
            "unpredictable": False,
            "reasoning": "周期预测理由",
            "features_used": ["stage1_diagnosis"],
        },
    }
    v = schema_test_validator()
    result = v.validate("stage2", json.dumps(payload), skip_next_bar=True)
    assert isinstance(result, Ok)
    assert isinstance(result.obj.get("next_bar_prediction"), dict)


def test_normalize_stage2_skip_next_bar_ui_path_omits_injection() -> None:
    """UI replay with feature off should not synthesize next_bar for display."""
    obj = {
        "decision": {
            "order_type": "不下单",
            "order_direction": None,
            "entry_price": None,
            "take_profit_price": None,
            "stop_loss_price": None,
            "reasoning": "test",
            "diagnosis_confidence": 55,
            "diagnosis_confidence_reasoning": "t",
            "trade_confidence": 55,
            "trade_confidence_reasoning": "t",
            "estimated_win_rate": None,
            "estimated_win_rate_reasoning": None,
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "t",
            "invalidation_condition": "t",
        },
        "diagnosis_summary": {
            "cycle_position": "trending_tr",
            "direction": "neutral",
            "key_signals": [],
        },
        "decision_trace": [],
        "terminal": {"node_id": "0", "outcome": "wait", "label": "test"},
    }
    out = normalize_stage2(obj, skip_next_bar=True)
    assert "next_bar_prediction" not in out
    assert isinstance(out.get("next_cycle_prediction"), dict)


def test_normalize_stage2_no_order_english_alias_passes_schema() -> None:
    """Regression: OpenClaw emits order_type=no_order + terminal=wait."""
    payload = {
        "decision": {
            "order_type": "no_order",
            "order_direction": None,
            "entry_price": None,
            "take_profit_price": None,
            "stop_loss_price": None,
            "reasoning": "等待更好 setup",
            "diagnosis_confidence": 70,
            "trade_confidence": 65,
            "estimated_win_rate": None,
            "key_factors": ["边界不清晰"],
            "watch_points": ["等待反弹"],
            "risk_assessment": "过渡期风险偏高",
        },
        "diagnosis_summary": {
            "cycle_position": "trending_tr",
            "direction": "bearish",
            "key_signals": [],
        },
        "bar_analysis": {
            "always_in": "short",
            "last_closed_bar": "K1",
            "bar_type": "trend_bear",
            "signal_bar": {
                "bar": None,
                "quality": "invalid",
                "pattern": "no_signal",
            },
            "entry_bar": {
                "strength": "not_triggered",
                "follow_through": False,
                "freshness": "pending",
            },
            "second_entry": {"is_second_entry": False, "type": "none"},
        },
        "decision_trace": [
            {
                "node_id": "10.1",
                "question": "是否能明确止损？",
                "answer": "no",
                "reason": "无结构止损",
                "bar_range": "K8-K1",
                "skipped": False,
            },
            {
                "node_id": "10.2",
                "question": "止损是否过大？",
                "answer": None,
                "reason": "跳过",
                "bar_range": None,
                "skipped": True,
            },
            {
                "node_id": "14",
                "question": "是否触犯禁止行为？",
                "answer": "no",
                "reason": "未触犯",
                "bar_range": "K8-K1",
                "skipped": False,
            },
        ],
        "terminal": {"node_id": "10.1", "outcome": "wait"},
        "next_cycle_prediction": {
            "primary": "trading_range",
            "primary_probability": 30,
            "direction": "neutral",
            "reasoning": "可能进入区间",
            "probabilities": {
                "spike": 2,
                "micro_channel": 3,
                "tight_channel": 5,
                "normal_channel": 10,
                "broad_channel": 25,
                "trending_tr": 20,
                "trading_range": 30,
                "extreme_tr": 5,
            },
        },
    }
    out = normalize_stage2(payload)
    assert out["decision"]["order_type"] == "不下单"
    assert out["decision"]["estimated_win_rate_reasoning"] is None
    assert out["bar_analysis"]["signal_bar"]["pattern"] == "none"
    assert out["next_cycle_prediction"]["cycle"] == "trading_range"

    result = schema_test_validator().validate(
        "stage2",
        json.dumps(out, ensure_ascii=False),
    )
    assert isinstance(result, Ok)


def test_normalize_flat_decision_wait_string_from_user_report() -> None:
    """Models sometimes emit decision=wait with fields at root instead of nested."""
    payload = {
        "decision": "wait",
        "decision_trace": [
            {
                "node": "§6.1",
                "question": "是否满足区间态（清晰上下边界）？",
                "answer": "是",
                "reason": "上沿60857测试≥2次",
            },
            {
                "node": "§9.0P",
                "question": "AIS下做空限价是否可行？",
                "answer": "否",
                "reason": "结构止损过窄",
            },
            {
                "node": "§14 禁止项扫描",
                "question": "是否触犯禁止行为？",
                "answer": "否",
                "reason": "未触犯",
            },
        ],
        "reasoning": "direction=neutral且AIS下，当前无合格空头方案，等待更明确信号。",
        "key_factors": ["K1站上EMA20", "AIS仍在"],
        "risk_assessment": "方向矛盾，强行入场风险较高。",
        "watch_points": ["K0若为空头趋势棒则重新评估"],
        "terminal": {"outcome": "wait", "node_id": "9.0P"},
    }
    stage1 = {
        "cycle_position": "trending_tr",
        "direction": "neutral",
        "diagnosis_confidence": 62,
        "bar_analysis": {
            "always_in": "short",
            "last_closed_bar": "K1",
            "bar_type": "trend_bull",
            "signal_bar": {"bar": None, "quality": "invalid", "reason": "无"},
            "entry_setup_type": "none",
            "follow_through": "pending",
        },
    }
    out = normalize_stage2(payload, stage1_json=stage1, skip_next_bar=True)
    decision = out["decision"]
    assert isinstance(decision, dict)
    assert decision["order_type"] == "不下单"
    assert "direction=neutral" in decision["reasoning"]
    assert decision["key_factors"] == ["K1站上EMA20", "AIS仍在"]
    assert decision["entry_price"] is None
    assert out["decision_trace"][-1]["node_id"] == "14.1"

    result = schema_test_validator().validate(
        "stage2",
        json.dumps(out, ensure_ascii=False),
        stage1_json=stage1,
        skip_next_bar=True,
    )
    assert isinstance(result, Ok), getattr(result, "message", result)


def test_repair_misplaced_decision_fields_from_diagnosis_summary() -> None:
    """Regression: models put estimated_win_rate_reasoning under diagnosis_summary."""
    payload = {
        "decision": {
            "order_type": "限价单",
            "order_direction": "做空",
            "entry_price": 7335.0,
            "stop_loss_price": 7337.51,
            "take_profit_price": 7319.84,
            "take_profit_price_2": 7309.34,
            "reasoning": "计划限价做空。",
            "estimated_win_rate": 60,
            "trade_confidence": 55,
            "trade_confidence_reasoning": "结构一致。",
            "diagnosis_confidence": 72,
            "diagnosis_confidence_reasoning": "阶段一诊断一致。",
            "key_factors": ["AIS确认"],
            "risk_assessment": "风险可控。",
            "watch_points": ["等待反弹"],
            "invalidation_condition": "突破止损",
        },
        "diagnosis_summary": {
            "cycle_position": "spike",
            "direction": "bearish",
            "estimated_win_rate_reasoning": "双顶结构偏空，胜率约60%。",
        },
        "decision_trace": [
            {
                "node_id": "14",
                "question": "是否触犯禁止行为？",
                "answer": "否",
                "reason": "未触犯",
                "bar_range": "K8-K1",
            }
        ],
        "terminal": {"node_id": "10.3", "outcome": "trade"},
        "next_bar_prediction": {
            "direction": "bearish",
            "probabilities": {"bullish": 20, "bearish": 60, "neutral": 20},
            "unpredictable": False,
            "reasoning": "偏空",
            "features_used": ["stage1_diagnosis"],
        },
        "next_cycle_prediction": {
            "current_cycle": "spike",
            "predicted_next_cycle": "broad_channel",
            "direction": "bearish",
            "confidence": 60,
            "reasoning": "可能进入宽通道。",
            "probabilities": {
                "spike": 3,
                "micro_channel": 5,
                "tight_channel": 8,
                "normal_channel": 20,
                "broad_channel": 35,
                "trending_tr": 15,
                "trading_range": 10,
                "extreme_tr": 4,
            },
            "features_used": ["stage1_diagnosis", "kline_features"],
        },
    }
    stage1 = {
        "cycle_position": "spike",
        "direction": "bearish",
        "key_signals": ["AIS确认"],
        "diagnosis_confidence": 72,
    }
    out = normalize_stage2(payload, stage1_json=stage1, skip_next_bar=True)
    assert out["decision"]["estimated_win_rate_reasoning"] == "双顶结构偏空，胜率约60%。"
    assert out["diagnosis_summary"]["key_signals"] == ["AIS确认"]
    assert "current_cycle" not in out["next_cycle_prediction"]
    assert out["next_cycle_prediction"]["cycle"] == "broad_channel"
    assert "estimated_win_rate_reasoning" not in out["diagnosis_summary"]


def test_coerce_breakout_without_basis_to_limit() -> None:
    from pa_agent.ai.stage2_normalizer import _coerce_breakout_without_basis

    out = {
        "decision": {
            "order_type": "突破单",
            "entry_basis_bar": None,
            "entry_basis_extreme": None,
            "entry_rule": "K1 high + 1 tick",
        }
    }
    assert _coerce_breakout_without_basis(out) is True
    assert out["decision"]["order_type"] == "限价单"
    assert out["decision"]["entry_rule"] is None
