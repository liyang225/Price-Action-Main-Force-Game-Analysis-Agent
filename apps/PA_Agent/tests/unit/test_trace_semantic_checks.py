"""Unit tests for trace semantic validation."""
from __future__ import annotations

import json

from pa_agent.ai.stage1_normalizer import normalize_stage1
from pa_agent.ai.trace_semantic_checks import (
    _question_matches_tree,
    validate_trace_semantics,
)


def test_empty_reason_ok_for_intermediate_node() -> None:
    trace = [
        {
            "node_id": "1.2",
            "question": "是否能识别市场周期？",
            "answer": "是",
            "reason": "",
            "bar_range": "K8-K1",
        }
    ]
    errs = validate_trace_semantics(trace, path_prefix="gate_trace", stage="stage1")
    assert not any("non-empty" in e for e in errs)


def test_empty_reason_fails_for_10_3() -> None:
    trace = [
        {
            "node_id": "10.3",
            "question": "交易者方程是否成立？",
            "answer": "是",
            "reason": "   ",
            "bar_range": "K1",
        }
    ]
    errs = validate_trace_semantics(trace, path_prefix="decision_trace", stage="stage2")
    assert any("non-empty" in e for e in errs)


def test_empty_reason_fails_legacy_0_1() -> None:
    """0.1 is optional-reason; empty is allowed (legacy test name kept for grep stability)."""
    trace = [
        {
            "node_id": "0.1",
            "question": "是否看得懂当前市场？",
            "answer": "是",
            "reason": "   ",
            "bar_range": "K3-K1",
        }
    ]
    errs = validate_trace_semantics(trace, path_prefix="gate_trace", stage="stage1")
    assert not any("non-empty" in e for e in errs)


def test_short_reason_ok_if_substantive() -> None:
    trace = [
        {
            "node_id": "1.1",
            "question": "数据是否足够？",
            "answer": "是",
            "reason": "提供了100根K线，数据充足",
            "bar_range": "K100-K1",
        }
    ]
    errs = validate_trace_semantics(trace, path_prefix="gate_trace", stage="stage1")
    assert not any("chars required" in e for e in errs)


def test_proceed_requires_final_rationale() -> None:
    trace = [
        {
            "node_id": "2.5",
            "question": "惯性强度",
            "answer": "是",
            "reason": "惯性足够，结构支持继续跟踪当前方向判断。",
            "bar_range": "K4-K1",
        }
    ]
    errs = validate_trace_semantics(
        trace,
        path_prefix="gate_trace",
        stage="stage1",
        gate_result="proceed",
    )
    assert any("last gate_trace" in e for e in errs)


def test_always_in_question_fuzzy_match() -> None:
    expected = "当前是否处于 Always In 状态？"
    assert _question_matches_tree(expected, "当前是否处于Always In状态？")


def test_channel_direction_question_paraphrase() -> None:
    expected = "通道方向是上涨还是下跌？"
    assert _question_matches_tree(
        expected, "通道方向是否为下跌？", node_id="4.2"
    )


def test_user_gate_trace_passes_after_normalize() -> None:
    raw = {
        "gate_result": "proceed",
        "gate_trace": [
            {
                "node_id": "1.1",
                "question": "数据是否足够？",
                "answer": "是",
                "reason": "提供了100根K线，数据充足",
                "bar_range": "K100-K1",
            },
            {
                "node_id": "1.3",
                "question": "当前市场是否极端混乱？",
                "answer": "否",
                "reason": "虽有震荡，但边界清晰，未陷入铁丝网式的极端混乱",
                "bar_range": "K30-K1",
            },
            {
                "node_id": "2.4",
                "question": "当前是否处于Always In状态？",
                "answer": "否",
                "reason": "价格已跌破20EMA，未持续位于均线上方，也未形成清晰的Always In Short结构，呈中性",
                "bar_range": "K15-K1",
            },
            {
                "node_id": "2.5",
                "question": "当前惯性强度是否足够支撑趋势跟踪？",
                "answer": "否",
                "reason": "近期回撤较深、K线重叠度高，缺乏连续强趋势棒，惯性强度不足，不宜追击趋势",
                "bar_range": "K30-K1",
            },
        ],
    }
    out = normalize_stage1(raw, normalization_mode="strict")
    errs = validate_trace_semantics(
        out["gate_trace"],
        path_prefix="gate_trace",
        stage="stage1",
        gate_result="proceed",
    )
    assert errs == [], errs
