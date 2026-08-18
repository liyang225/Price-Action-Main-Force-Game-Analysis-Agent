"""Tests for gate/decision trace normalization."""
from __future__ import annotations

import json

from pa_agent.ai.json_validator import Ok
from tests.fixtures.validators import schema_test_validator
from pa_agent.ai.stage2_normalizer import normalize_stage2
from pa_agent.ai.trace_normalize import (
    fix_bar_range_string,
    normalize_stage2_traces,
    normalize_trace_item,
    normalize_trace_list,
)
from tests.integration.conftest import VALID_STAGE2


def test_fix_reversed_bar_range() -> None:
    assert fix_bar_range_string("K1-K4") == "K4-K1"
    assert fix_bar_range_string("K50-K1") == "K50-K1"


def test_normalize_trace_item_maps_zhongli_to_neutral() -> None:
    item = {
        "node_id": "2.2",
        "question": "长程大背景方向与近期方向的关系？",
        "answer": "中立",
        "reason": "test",
        "bar_range": "K40-K1",
    }
    normalize_trace_item(item, normalization_mode="strict")
    assert item["answer"] == "中性"


def test_gate_22_conflict_answer_mapped_to_yes() -> None:
    from pa_agent.ai.trace_normalize import normalize_stage1_traces

    obj = {
        "gate_result": "proceed",
        "gate_trace": [
            {
                "node_id": "2.2",
                "question": "长程大背景方向与近期方向的关系？",
                "answer": "冲突",
                "branch": "neutral_background",
                "reason": "长程空头背景，近期多头",
                "bar_range": "K100-K1",
            },
        ],
    }
    normalize_stage1_traces(obj)
    item = obj["gate_trace"][0]
    assert item["answer"] == "是"
    assert item["branch"] == "neutral_background"


def test_gate_end_removed_and_proceed_answer_fixed() -> None:
    from pa_agent.ai.trace_normalize import normalize_stage1_traces

    obj = {
        "gate_result": "proceed",
        "direction": "bullish",
        "gate_trace": [
            {
                "node_id": "2.5",
                "question": "惯性强度",
                "answer": "否",
                "reason": "惯性不足",
                "bar_range": "K8-K1",
            },
            {
                "node_id": "gate_end",
                "question": "闸门是否通过？",
                "answer": "proceed",
                "reason": "闸门通过",
                "bar_range": "K8-K1",
            },
        ],
    }
    normalize_stage1_traces(obj)
    assert len(obj["gate_trace"]) == 1
    assert obj["gate_trace"][0]["node_id"] == "2.5"
    assert "进入阶段二" in obj["gate_trace"][0]["reason"] or "闸门通过" in obj["gate_trace"][0]["reason"]


def test_strip_program_reference_from_gate_13_and_25() -> None:
    from pa_agent.ai.trace_normalize import normalize_stage1_traces

    long_prog = "程序长块" * 30
    obj = {
        "gate_result": "proceed",
        "gate_trace": [
            {
                "node_id": "1.3",
                "question": "q",
                "answer": "否",
                "reason": f"AI简述【程序参考数据（K5-K1）：{long_prog}】",
                "bar_range": "K5-K1",
            },
            {
                "node_id": "2.5",
                "question": "q",
                "answer": "否",
                "reason": f"AI简述【程序参考数据（K8-K1）：{long_prog}】",
                "bar_range": "K8-K1",
            },
        ],
    }
    normalize_stage1_traces(obj)
    for item in obj["gate_trace"]:
        assert "程序参考数据" not in item["reason"]
        assert item["reason"].startswith("AI简述")


def test_strip_program_reference_from_gate_25() -> None:
    from pa_agent.ai.trace_normalize import normalize_stage1_traces

    long_prog = "程序长块" * 30
    obj = {
        "gate_result": "proceed",
        "gate_trace": [
            {
                "node_id": "2.5",
                "question": "q",
                "answer": "否",
                "reason": f"AI简述【程序参考数据（K8-K1）：{long_prog}】",
                "bar_range": "K8-K1",
            },
        ],
    }
    normalize_stage1_traces(obj)
    assert "程序参考数据" not in obj["gate_trace"][0]["reason"]
    assert obj["gate_trace"][0]["reason"].startswith("AI简述")


def test_pending_bar_range_inferred_from_reason() -> None:
    """Regression: pending entry bar_range on §9.7 -> K{n} from reason text."""
    item = {
        "node_id": "9.7",
        "question": "入场棒是否强势且有跟随？",
        "answer": "不适用",
        "reason": "入场棒尚未触发，挂突破单等待K3低点被跌破。",
        "bar_range": "pending",
        "skipped": False,
    }
    normalize_trace_item(item, default_max_seq=50, normalization_mode="strict")
    assert item["bar_range"] == "K3"


def test_fix_comma_separated_bar_range() -> None:
    assert fix_bar_range_string("K1,K7") == "K7-K1"
    assert fix_bar_range_string("K7、K1") == "K7-K1"


def test_expand_bar_range_when_reason_cites_older_k() -> None:
    """Regression: 9.4 reason 'K4之后' with bar_range K2 -> K4-K2."""
    item = {
        "node_id": "9.4",
        "answer": "是",
        "reason": "此为K4之后第一次明确反弹做空信号(L1)，非二次入场。",
        "bar_range": "K2",
    }
    normalize_trace_item(item, default_max_seq=20, normalization_mode="strict")
    assert item["bar_range"] == "K4-K2"


def test_global_bar_range_strict_uses_inferred_max() -> None:
    trace = [
        {"node_id": "10.1", "answer": "是", "bar_range": "K20-K1"},
        {"node_id": "14.1", "answer": "否", "bar_range": "全局"},
    ]
    normalize_trace_list(trace, normalization_mode="strict")
    assert trace[1]["bar_range"] == "K20-K1"


def test_global_bar_range_uses_trace_max() -> None:
    item = {"node_id": "14.1", "answer": "通过", "bar_range": "全局"}
    normalize_trace_item(
        item,
        default_max_seq=100,
        normalization_mode="lenient",
    )
    assert item["answer"] == "是"
    assert item["bar_range"] == "K100-K1"


def test_node_63_composite_boundary_answer() -> None:
    item = {
        "node_id": "6.3",
        "question": "当前价格是否在区间边界？",
        "answer": "是，在下边界",
        "reason": "x",
        "bar_range": "K5-K1",
    }
    normalize_trace_item(item, normalization_mode="lenient")
    assert item["answer"] == "是"
    assert item["branch"] == "lower"


def test_node_62_trending_tr_answer() -> None:
    item = {
        "node_id": "6.2",
        "question": "区间类型",
        "answer": "趋势型交易区间",
        "reason": "x",
        "bar_range": "K25-K1",
    }
    normalize_trace_item(item, normalization_mode="lenient")
    assert item["answer"] == "是"
    assert item["branch"] == "trending_tr"


def test_node_42_directional_answer() -> None:
    item = {
        "node_id": "4.2",
        "question": "通道方向",
        "answer": "下跌",
        "reason": "x",
        "bar_range": "K100-K1",
    }
    normalize_trace_item(item, normalization_mode="lenient")
    assert item["answer"] == "是"
    assert item["branch"] == "bearish"


def test_validator_accepts_user_trending_tr_trace() -> None:
    """Regression: AI wrote 是，在下边界 / 趋势型交易区间 as answer."""
    payload = normalize_stage2(
        {
            **VALID_STAGE2,
            "decision": {
                **VALID_STAGE2["decision"],
                "order_type": "不下单",
                "order_direction": None,
                "entry_price": None,
                "take_profit_price": None,
                "stop_loss_price": None,
            },
            "decision_trace": [
                {
                    "node_id": "6.2",
                    "question": "是普通交易区间还是趋势型交易区间？",
                    "answer": "趋势型交易区间",
                    "reason": "EMA下倾",
                    "bar_range": "K25-K1",
                },
                {
                    "node_id": "6.3",
                    "question": "当前价格是否在区间边界？",
                    "answer": "是，在下边界",
                    "reason": "近下边界",
                    "bar_range": "K5-K1",
                },
            ],
            "terminal": {
                "node_id": "9.1",
                "outcome": "wait",
                "label": "等待信号",
            },
        }
    )
    result = schema_test_validator().validate("stage2", json.dumps(payload, ensure_ascii=False))
    assert isinstance(result, Ok)


def test_null_bar_range_on_skipped_nodes() -> None:
    trace = [
        {
            "node_id": "10.1",
            "question": "是否能明确止损？",
            "answer": "不适用",
            "reason": "无交易计划",
            "skipped": True,
            "bar_range": None,
        },
        {
            "node_id": "10.2",
            "question": "止损是否过大？",
            "answer": "不适用",
            "reason": "无交易计划",
            "skipped": True,
            "bar_range": None,
        },
    ]
    normalize_trace_list(trace, default_max_seq=48, normalization_mode="lenient")
    assert trace[0]["bar_range"] == "不适用"
    assert trace[1]["bar_range"] == "不适用"


def test_null_bar_range_inherits_prior_range() -> None:
    trace = [
        {
            "node_id": "9.1",
            "question": "信号？",
            "answer": "否",
            "reason": "x",
            "bar_range": "K48-K1",
        },
        {
            "node_id": "5.1",
            "question": "微型通道？",
            "answer": "否",
            "reason": "回撤大",
            "bar_range": None,
        },
    ]
    normalize_trace_list(trace, default_max_seq=48, normalization_mode="lenient")
    assert trace[1]["bar_range"] == "K48-K1"


def test_validator_accepts_user_payload_with_null_bar_ranges() -> None:
    """Regression: skipped §10/§11 nodes had bar_range: null."""
    payload = normalize_stage2(
        {
            **VALID_STAGE2,
            "decision": {
                **VALID_STAGE2["decision"],
                "order_type": "不下单",
                "order_direction": None,
                "entry_price": None,
                "take_profit_price": None,
                "stop_loss_price": None,
            },
            "decision_trace": [
                {
                    "node_id": "4.2",
                    "question": "通道方向是上涨还是下跌？",
                    "answer": "下跌",
                    "reason": "LL+LH",
                    "bar_range": "K48-K1",
                },
                {
                    "node_id": "10.1",
                    "question": "是否能明确止损？",
                    "answer": "不适用",
                    "reason": "无交易计划",
                    "skipped": True,
                    "bar_range": None,
                },
                {
                    "node_id": "10.3",
                    "question": "交易者方程是否通过？",
                    "answer": "不适用",
                    "reason": "无交易计划",
                    "skipped": True,
                    "bar_range": None,
                },
            ],
            "terminal": {
                "node_id": "9.1",
                "outcome": "wait",
                "label": "等待有效信号",
            },
        }
    )
    result = schema_test_validator().validate("stage2", json.dumps(payload, ensure_ascii=False))
    assert isinstance(result, Ok)
    assert result.obj["decision_trace"][1]["bar_range"] == "不适用"


def test_validator_accepts_normalized_user_stage2_snippet() -> None:
    base = normalize_stage2(
        {
            **VALID_STAGE2,
            "decision": {
                **VALID_STAGE2["decision"],
                "order_type": "不下单",
                "order_direction": None,
                "entry_price": None,
                "take_profit_price": None,
                "stop_loss_price": None,
            },
            "decision_trace": [
                {
                    "node_id": "4.2",
                    "question": "通道方向是上涨还是下跌？",
                    "answer": "下跌",
                    "reason": "LL+LH",
                    "bar_range": "K100-K1",
                },
                {
                    "node_id": "9.4",
                    "question": "是否是第一次入场？",
                    "answer": "是",
                    "reason": "Low1",
                    "bar_range": "K1-K4",
                },
                {
                    "node_id": "14.1",
                    "question": "禁止行为清单扫描",
                    "answer": "通过",
                    "reason": "ok",
                    "bar_range": "全局",
                },
            ],
            "terminal": {
                "node_id": "10.3",
                "outcome": "reject",
                "label": "交易者方程未通过",
            },
        }
    )
    result = schema_test_validator().validate("stage2", json.dumps(base, ensure_ascii=False))
    assert isinstance(result, Ok)


def test_partial_and_pending_answer_synonyms() -> None:
    """Regression: AI used 部分 / 待确认 instead of enum answers."""
    item_partial = {
        "node_id": "9.2",
        "question": "信号K线方向是否与计划方向一致？",
        "answer": "部分",
        "reason": "x",
        "bar_range": "K1",
    }
    normalize_trace_item(item_partial, normalization_mode="lenient")
    assert item_partial["answer"] == "中性"

    item_partial_fit = {
        "node_id": "5.5",
        "question": "是否为收缩台阶？",
        "answer": "部分符合",
        "reason": "x",
        "bar_range": "K55-K14",
    }
    normalize_trace_item(item_partial_fit, normalization_mode="lenient")
    assert item_partial_fit["answer"] == "中性"

    item_partial_yes = {
        "node_id": "6.1",
        "question": "是否存在清晰上下边界？",
        "answer": "部分是",
        "reason": "x",
        "bar_range": "K10-K1",
    }
    normalize_trace_item(item_partial_yes, normalization_mode="lenient")
    assert item_partial_yes["answer"] == "中性"

    item_yes_partial = {
        "node_id": "6.2",
        "question": "是否仍在通道内运行？",
        "answer": "是部分",
        "reason": "x",
        "bar_range": "K10-K1",
    }
    normalize_trace_item(item_yes_partial, normalization_mode="strict")
    assert item_yes_partial["answer"] == "中性"

    item_channel = {
        "node_id": "4.2",
        "question": "通道方向？",
        "answer": "上涨通道",
        "reason": "x",
        "bar_range": "K100-K14",
    }
    normalize_trace_item(item_channel, normalization_mode="lenient")
    assert item_channel["answer"] == "是"
    assert item_channel.get("branch") == "bullish"

    item_pending = {
        "node_id": "9.5",
        "question": "是否有跟随？",
        "answer": "待确认",
        "reason": "x",
        "bar_range": "K1",
    }
    normalize_trace_item(item_pending, normalization_mode="lenient")
    assert item_pending["answer"] == "等待"


def test_validator_accepts_partial_and_pending_answers() -> None:
    payload = normalize_stage2(
        {
            **VALID_STAGE2,
            "decision": {
                **VALID_STAGE2["decision"],
                "order_type": "不下单",
                "order_direction": None,
                "entry_price": None,
                "take_profit_price": None,
                "stop_loss_price": None,
            },
            "decision_trace": [
                {
                    "node_id": "9.2",
                    "question": "信号K线方向是否与计划方向一致？",
                    "answer": "部分",
                    "reason": "方向大致一致但质量一般",
                    "bar_range": "K1",
                },
                {
                    "node_id": "9.5",
                    "question": "是否有跟随？",
                    "answer": "待确认",
                    "reason": "尚无后续K线",
                    "bar_range": "K1",
                },
            ],
            "terminal": {
                "node_id": "10.3",
                "outcome": "wait",
                "label": "等待",
            },
        }
    )
    result = schema_test_validator().validate("stage2", json.dumps(payload, ensure_ascii=False))
    assert isinstance(result, Ok)
    assert result.obj["decision_trace"][0]["answer"] == "中性"
    assert result.obj["decision_trace"][1]["answer"] == "等待"


def test_validator_accepts_stage2_with_null_bar_range_and_forbid_phrase() -> None:
    """Regression: null bar_range, missing reason, §14 answer typo, node_id '14'."""
    payload = normalize_stage2(
        {
            **VALID_STAGE2,
            "decision": {
                **VALID_STAGE2["decision"],
                "order_type": "不下单",
                "order_direction": None,
                "entry_price": None,
                "take_profit_price": None,
                "stop_loss_price": None,
            },
            "decision_trace": [
                {
                    "node_id": "9.3",
                    "section": "入场信号",
                    "question": "信号棒是否过长？",
                    "answer": "不适用",
                    "skipped": True,
                    "bar_range": None,
                },
                {
                    "node_id": "14",
                    "section": "禁止行为",
                    "question": "是否触犯禁止行为？",
                    "answer": "无交易计划，不存在触犯",
                    "reason": "未触发入场",
                    "skipped": True,
                    "bar_range": None,
                },
            ],
            "terminal": {
                "node_id": "9.1",
                "outcome": "wait",
                "label": "等待",
            },
        }
    )
    result = schema_test_validator().validate("stage2", json.dumps(payload, ensure_ascii=False))
    assert isinstance(result, Ok)
    trace = result.obj["decision_trace"]
    # Find node 9.3 by id (position may vary due to program node injection)
    node_93 = next((n for n in trace if n.get("node_id") == "9.3"), None)
    assert node_93 is not None
    assert node_93["bar_range"] == "不适用" or node_93.get("skipped")
    # Node 14 should exist and be normalized
    node_14 = next((n for n in trace if n.get("node_id") in ("14", "14.1")), None)
    assert node_14 is not None


def test_repair_stage2_terminal_when_103_no() -> None:
    """Regression: model ends at 9.5 but 10.3 answer=否 → terminal must be 10.3."""
    obj = {
        "decision": {"order_type": "不下单"},
        "decision_trace": [
            {"node_id": "9.5", "question": "q", "answer": "否", "reason": "无跟随", "bar_range": "K2-K1"},
            {"node_id": "10.3", "question": "交易者方程是否通过？", "answer": "否", "reason": "方程不通过", "bar_range": "K1"},
        ],
        "terminal": {"node_id": "9.5", "outcome": "wait", "label": "等待"},
    }
    normalize_stage2_traces(obj, normalization_mode="strict")
    assert obj["terminal"]["node_id"] == "10.3"


def test_repair_stage2_canonical_question_42() -> None:
    obj = {
        "decision": {"order_type": "不下单"},
        "decision_trace": [
            {
                "node_id": "4.2",
                "question": "通道方向是否为下跌？",
                "answer": "是",
                "reason": "LL+LH",
                "bar_range": "K48-K1",
            },
        ],
        "terminal": {"node_id": "4.2", "outcome": "wait", "label": "x"},
    }
    normalize_stage2_traces(obj, normalization_mode="strict")
    assert obj["decision_trace"][0]["question"] == "通道方向是上涨还是下跌？"
