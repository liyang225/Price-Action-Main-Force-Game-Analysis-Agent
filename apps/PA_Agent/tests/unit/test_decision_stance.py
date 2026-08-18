from __future__ import annotations

from pa_agent.ai.decision_stance import (
    build_decision_stance_guidance,
    normalize_stance,
    stance_label_zh,
)


def test_normalize_stance_defaults_unknown_to_conservative():
    assert normalize_stance(None) == "conservative"
    assert normalize_stance("") == "conservative"
    assert normalize_stance("均衡") == "balanced"
    assert normalize_stance("aggressive") == "aggressive"


def test_stance_guidance_conservative_is_baseline():
    text = build_decision_stance_guidance("conservative")
    assert "保守" in text
    assert "当前系统默认" in text


def test_stance_guidance_balanced_more_aggressive_than_conservative():
    conservative = build_decision_stance_guidance("conservative")
    balanced = build_decision_stance_guidance("balanced")
    assert "次优但可执行" in balanced
    assert "次优但可执行" not in conservative


def test_stance_guidance_aggressive_more_than_balanced():
    balanced = build_decision_stance_guidance("balanced")
    aggressive = build_decision_stance_guidance("aggressive")
    assert "30–44" in aggressive
    assert "30–44" not in balanced


def test_stance_guidance_extreme_aggressive_forces_trade():
    aggressive = build_decision_stance_guidance("aggressive")
    extreme = build_decision_stance_guidance("extreme_aggressive")
    assert normalize_stance("极度激进") == "extreme_aggressive"
    assert "强制产出交易" in extreme
    assert "禁止因犹豫而输出「不下单」" in extreme
    assert "强制产出交易" not in aggressive


def test_stance_label_zh():
    assert stance_label_zh("balanced") == "均衡"
    assert stance_label_zh("extreme_aggressive") == "极度激进"
