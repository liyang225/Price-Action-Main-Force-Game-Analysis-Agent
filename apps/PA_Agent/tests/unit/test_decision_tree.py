"""Unit tests for binary decision tree helpers."""
from __future__ import annotations

from pa_agent.ai.decision_tree import (
    build_stage2_gate_wait_response,
    format_bar_basis_suffix,
    format_trace_answer,
    load_decision_tree,
    merge_traces,
    plain_trace_question,
    question_with_bar_basis,
    strip_question_bar_basis_suffix,
    validate_gate_result_consistency,
    validate_stage2_trace_consistency,
)
from tests.integration.conftest import SAMPLE_DECISION_TRACE, SAMPLE_GATE_TRACE, VALID_STAGE2


def test_question_with_bar_basis() -> None:
    item = {
        "node_id": "5.1",
        "question": "是否为微型通道？",
        "bar_range": "K50-K1",
    }
    assert question_with_bar_basis(item) == "是否为微型通道？（基于K50-K1判断）"
    assert format_bar_basis_suffix(item) == "（基于K50-K1判断）"
    assert plain_trace_question(item) == "是否为微型通道？"


def test_strip_question_bar_basis_suffix() -> None:
    q = "是否满足尖峰条件？（基于K15-K8判断）"
    assert strip_question_bar_basis_suffix(q) == "是否满足尖峰条件？"


def test_format_trace_answer_with_branch() -> None:
    item = {"answer": "是", "branch": "bearish"}
    assert format_trace_answer(item) == "是（空头）"


def test_load_decision_tree_has_sections() -> None:
    tree = load_decision_tree()
    assert len(tree["sections"]) >= 10
    assert "0.1" in tree["node_index"]


def test_node_branch_outcomes_from_txt() -> None:
    tree = load_decision_tree()
    n01 = tree["node_index"]["0.1"]
    assert "继续" in n01.get("branch_yes", "")
    assert "等待" in n01.get("branch_no", "")
    n63 = tree["node_index"]["6.3"]
    assert "下边界" in n63.get("branch_yes", "") or "看涨" in n63.get("branch_yes", "")


def test_merge_traces_order() -> None:
    merged = merge_traces(SAMPLE_GATE_TRACE, SAMPLE_DECISION_TRACE)
    assert merged[0]["phase"] == "gate"
    assert merged[-1]["phase"] == "decision"


def test_gate_wait_shortcircuit_response() -> None:
    stage1 = {
        "cycle_position": "unknown",
        "direction": "neutral",
        "diagnosis_confidence": 20,
        "key_signals": [],
        "gate_trace": [
            {
                "node_id": "1.2",
                "question": "是否能识别市场周期？",
                "answer": "否",
                "reason": "无法归类",
            }
        ],
        "gate_result": "wait",
        "risk_warning": "诊断不清",
    }
    s2 = build_stage2_gate_wait_response(stage1)
    assert s2["decision"]["order_type"] == "不下单"
    assert s2["gate_shortcircuited"] is True
    assert s2["terminal"]["outcome"] == "wait"


def test_validate_gate_proceed_ok() -> None:
    from tests.integration.conftest import VALID_STAGE1

    assert not validate_gate_result_consistency(VALID_STAGE1)


def test_validate_stage2_trade_ok() -> None:
    assert not validate_stage2_trace_consistency(VALID_STAGE2)


def test_validate_stage2_no_order_trade_conflict() -> None:
    bad = {
        **VALID_STAGE2,
        "decision": {**VALID_STAGE2["decision"], "order_type": "不下单"},
        "terminal": {"node_id": "10.3", "outcome": "trade", "label": "x"},
    }
    errs = validate_stage2_trace_consistency(bad)
    assert any("不下单" in e for e in errs)


def test_gate_trace_forbids_node_0_3() -> None:
    from tests.integration.conftest import VALID_STAGE1

    bad = {
        **VALID_STAGE1,
        "gate_trace": [
            {
                "node_id": "0.3",
                "question": "交易者方程是否通过？",
                "answer": "是",
                "reason": "wrong stage",
                "bar_range": "K1",
            }
        ],
    }
    errs = validate_gate_result_consistency(bad)
    assert any("0.3" in e for e in errs)


def test_stage2_trade_requires_10_3_before_11() -> None:
    bad = {
        **VALID_STAGE2,
        "decision_trace": [
            {
                "node_id": "11.2",
                "question": "q",
                "answer": "是",
                "reason": "r",
                "bar_range": "K20-K1",
            },
            {
                "node_id": "10.3",
                "question": "交易者方程",
                "answer": "是",
                "reason": "r",
                "bar_range": "K1",
            },
        ],
    }
    errs = validate_stage2_trace_consistency(bad)
    assert any("10.3" in e or "11" in e for e in errs)


# ── T8: Short-circuit path tests for next_bar_prediction ─────────────────────


def test_gate_wait_response_contains_unpredictable_prediction() -> None:
    stage1 = {
        "cycle_position": "unknown",
        "direction": "neutral",
        "diagnosis_confidence": 20,
        "key_signals": [],
        "gate_trace": [
            {
                "node_id": "1.2",
                "question": "是否能识别市场周期？",
                "answer": "否",
                "reason": "无法归类",
            }
        ],
        "gate_result": "wait",
        "risk_warning": "诊断不清",
    }
    s2 = build_stage2_gate_wait_response(stage1)
    pred = s2.get("next_bar_prediction")
    assert isinstance(pred, dict), f"Expected dict, got {type(pred)}"
    assert pred["unpredictable"] is True
    assert pred["direction"] is None
    assert pred["probabilities"] is None


def test_gate_unknown_response_contains_unpredictable_prediction() -> None:
    stage1 = {
        "cycle_position": "extreme_tr",
        "direction": "neutral",
        "diagnosis_confidence": 10,
        "key_signals": [],
        "gate_trace": [
            {
                "node_id": "0.1",
                "question": "是否看得懂当前市场？",
                "answer": "否",
                "reason": "极端行情",
            }
        ],
        "gate_result": "unknown",
        "risk_warning": "极端",
    }
    s2 = build_stage2_gate_wait_response(stage1)
    pred = s2.get("next_bar_prediction")
    assert isinstance(pred, dict)
    assert pred["unpredictable"] is True


def test_gate_wait_prediction_passes_schema() -> None:
    """Short-circuit response with unpredictable prediction must pass full Stage 2 validation."""
    from pa_agent.ai.json_validator import Ok
    from tests.fixtures.validators import schema_test_validator

    stage1 = {
        "cycle_position": "unknown",
        "direction": "neutral",
        "diagnosis_confidence": 20,
        "key_signals": [],
        "gate_trace": [
            {
                "node_id": "1.2",
                "question": "是否能识别市场周期？",
                "answer": "否",
                "reason": "无法归类",
            }
        ],
        "gate_result": "wait",
        "risk_warning": "诊断不清",
    }
    s2 = build_stage2_gate_wait_response(stage1)
    import json
    result = schema_test_validator().validate("stage2", json.dumps(s2, ensure_ascii=False))
    assert isinstance(result, Ok), f"Expected Ok, got {result}"
