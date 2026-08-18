"""Tests for validation retry policy and feedback."""
from __future__ import annotations

from dataclasses import dataclass

from pa_agent.ai.retry_feedback import build_retry_feedback
from pa_agent.ai.retry_policy import detect_cheat, should_retry
from pa_agent.ai.stage2_normalizer import ensure_stage2_predictions
from pa_agent.gui.stage2_payload import prepare_stage2_for_ui


@dataclass
class _FakeErr:
    category: str
    message: str
    missing_fields: list[str]
    invalid_fields: list[str]
    parse_position: str | None = None
    raw_text: str = ""


class _Settings:
    retry_enabled = True
    retry_max = 2
    retry_max_semantic = 1
    retry_stage2 = True


def test_should_retry_format_errors():
    assert should_retry("b", [], ["gate_trace"], attempt=0, settings=_Settings())
    assert should_retry("c", ["node_overrides.0.answer"], [], attempt=0, settings=_Settings())
    assert should_retry("c", ["decision.reasoning"], [], attempt=0, settings=_Settings())
    assert not should_retry("c", ["metrics:bad"], [], attempt=0, settings=_Settings())


def test_build_retry_feedback_node_overrides_answer_hint():
    err = _FakeErr(
        "c",
        "'空头' is not one of ['是', '否', '中性', '等待', '不适用']",
        [],
        ["node_overrides.0.answer"],
    )
    text = build_retry_feedback(err, stage="stage1", attempt=1, max_attempts=3)
    assert "node_overrides answer" in text
    assert "branch" in text


def test_detect_cheat_immutable_direction():
    before = {"direction": "bullish", "cycle_position": "spike", "gate_result": "proceed"}
    after = {"direction": "bearish", "cycle_position": "spike", "gate_result": "proceed"}
    flags = detect_cheat("stage1", before, after)
    assert any("direction" in f for f in flags)


def test_detect_cheat_allows_direction_with_incremental_override():
    before = {"direction": "neutral", "cycle_position": "trading_range", "gate_result": "proceed"}
    after = {"direction": "bearish", "cycle_position": "trading_range", "gate_result": "proceed"}
    after_raw = {
        **after,
        "node_overrides": [
            {
                "node_id": "2.3",
                "answer": "是",
                "branch": "bearish",
                "override_reason": "K1 trend_bear broke support.",
            }
        ],
        "incremental_delta": {
            "new_closed_bars": ["K1"],
            "changed_fields": ["direction"],
            "summary": "direction neutral→bearish",
        },
    }
    flags = detect_cheat(
        "stage1",
        before,
        after,
        before_raw={**before, "direction": "neutral"},
        after_raw=after_raw,
    )
    assert not any("direction" in f for f in flags)


def test_detect_cheat_ignores_gate_result_when_normalizer_repairs_to_same():
    before = {"direction": "neutral", "cycle_position": "broad_channel", "gate_result": "proceed"}
    after = {"direction": "neutral", "cycle_position": "broad_channel", "gate_result": "proceed"}
    flags = detect_cheat(
        "stage1",
        before,
        after,
        before_raw={**before, "gate_result": "proceed"},
        after_raw={**after, "gate_result": "unknown"},
    )
    assert not any("gate_result" in f for f in flags)


def test_detect_cheat_flags_raw_gate_weakening():
    before = {"direction": "neutral", "cycle_position": "broad_channel", "gate_result": "proceed"}
    after = {"direction": "neutral", "cycle_position": "broad_channel", "gate_result": "unknown"}
    flags = detect_cheat(
        "stage1",
        before,
        after,
        before_raw={**before, "gate_result": "proceed"},
        after_raw={**after, "gate_result": "unknown"},
    )
    assert any("gate_result" in f for f in flags)


def test_detect_cheat_no_false_positive_when_program_normalizes_direction():
    """Raw AI direction may differ from post-normalize value; compare normalized copies."""
    from pa_agent.ai.json_validator import JsonValidator
    from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame

    bars = tuple(
        KlineBar(
            seq=i + 1,
            ts_open=float(1_000_000 - (i + 1) * 60_000),
            open=2000.0,
            high=2010.0,
            low=1990.0,
            close=2005.0,
            volume=1.0,
            closed=True,
        )
        for i in range(25)
    )
    frame = KlineFrame(
        symbol="TEST",
        timeframe="1h",
        bars=bars,
        snapshot_ts_local_ms=1,
        indicators=IndicatorBundle(
            ema20=tuple([2000.0] * 25),
            atr14=tuple([10.0] * 25),
        ),
    )
    validator = JsonValidator()
    raw = {
        "direction": "bearish",
        "cycle_position": "broad_channel",
        "gate_result": "proceed",
        "gate_trace": [],
    }
    before_norm = validator.normalize_parsed("stage1", raw, kline_frame=frame)
    after_norm = validator.normalize_parsed("stage1", dict(raw), kline_frame=frame)
    flags = detect_cheat("stage1", before_norm, after_norm)
    assert not flags


def test_build_retry_feedback_contains_stage():
    err = _FakeErr("b", "missing", ["next_bar_prediction"], [], None, "{}")
    text = build_retry_feedback(err, stage="stage2", attempt=1, max_attempts=2)
    assert "next_bar_prediction" in text
    assert "阶段二" in text


def test_build_retry_feedback_decision_trace_answer_hint():
    err = _FakeErr(
        "c",
        "'是部分' is not one of ['是', '否', '中性', '等待', '不适用']",
        [],
        ["decision_trace.3.answer"],
    )
    text = build_retry_feedback(err, stage="stage2", attempt=1, max_attempts=3)
    assert "decision_trace answer" in text
    assert "是部分" in text


def test_build_retry_feedback_json_syntax_fence_hint():
    err = _FakeErr(
        "a",
        "Expecting property name enclosed in double quotes",
        [],
        [],
        parse_position="line 1 column 2",
        raw_text='```json\n{"broken":',
    )
    text = build_retry_feedback(
        err,
        stage="stage2",
        attempt=1,
        max_attempts=3,
        previous_raw='```json\n{"broken":',
    )
    assert "JSON 语法提示" in text
    assert "围栏" in text


def test_ensure_stage2_predictions_for_old_record():
    s2 = {
        "decision": {"order_type": "不下单", "reasoning": "等待"},
        "diagnosis_summary": {"cycle_position": "broad_channel", "direction": "neutral"},
        "decision_trace": [],
        "terminal": {"node_id": "9.0", "outcome": "wait", "label": "x"},
    }
    assert ensure_stage2_predictions(s2) is True
    assert isinstance(s2.get("next_bar_prediction"), dict)
    assert isinstance(s2.get("next_cycle_prediction"), dict)


def test_prepare_stage2_for_ui_merges_predictions():
    s2 = {
        "decision": {"order_type": "不下单"},
        "diagnosis_summary": {"cycle_position": "broad_channel", "direction": "neutral"},
    }
    payload = prepare_stage2_for_ui(s2)
    assert "next_bar_prediction" in payload
    assert "next_cycle_prediction" in payload
