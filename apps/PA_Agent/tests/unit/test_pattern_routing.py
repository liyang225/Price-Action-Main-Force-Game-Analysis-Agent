"""Tests for pattern routing overlays and stage-1 pattern coherence."""
from __future__ import annotations

from pa_agent.ai.coherence_checks import validate_stage1_coherence
from pa_agent.ai.pattern_routing import (
    merge_detected_patterns,
    validate_detected_patterns_vs_key_signals,
)
from pa_agent.ai.router import route_strategy_files


def test_merge_detected_patterns_from_entry_setup_wedge() -> None:
    s1 = {
        "cycle_position": "normal_channel",
        "direction": "bullish",
        "detected_patterns": [],
        "bar_analysis": {"entry_setup_type": "wedge"},
    }
    assert merge_detected_patterns(s1) == ["wedge"]


def test_route_loads_file14_when_entry_setup_wedge_only() -> None:
    s1 = {
        "cycle_position": "normal_channel",
        "direction": "bullish",
        "detected_patterns": [],
        "bar_analysis": {"entry_setup_type": "wedge"},
    }
    files = route_strategy_files(s1)
    assert "文件14-楔形形态分析交易.txt" in files


def test_route_loads_file18_on_breakout_pullback_entry_setup() -> None:
    s1 = {
        "cycle_position": "broad_channel",
        "direction": "bullish",
        "detected_patterns": [],
        "bar_analysis": {"entry_setup_type": "breakout_pullback"},
    }
    files = route_strategy_files(s1)
    assert "文件18-突破失败与突破测试.txt" in files


def test_trading_range_syncs_range_patterns_from_text() -> None:
    s1 = {
        "cycle_position": "trading_range",
        "direction": "neutral",
        "detected_patterns": [],
        "key_signals": ["K线重叠度高，多空力量均衡"],
        "risk_warning": "区间下沿若被突破，警惕假突破后快速收复",
        "bar_analysis": {"entry_setup_type": "none"},
    }
    from pa_agent.ai.stage1_normalizer import normalize_stage1

    out = normalize_stage1(s1, normalization_mode="strict")
    tags = set(out["detected_patterns"])
    assert "breakout_failure" in tags
    assert "middle_range" in tags or "overlap" in tags


def test_transition_role_mapped_to_structure() -> None:
    from pa_agent.ai.stage1_normalizer import normalize_stage1

    out = normalize_stage1(
        {
            "bar_by_bar_summary": [
                {"bar": "K1", "role": "transition", "bar_type": "inside", "reason": "x" * 8}
            ]
        },
        normalization_mode="strict",
    )
    assert out["bar_by_bar_summary"][0]["role"] == "structure"


def test_key_signals_wedge_without_detected_patterns_fails_coherence() -> None:
    s1 = {
        "gate_result": "wait",
        "gate_trace": [{"node_id": "1.2", "answer": "否", "bar_range": "K5-K1"}],
        "cycle_position": "unknown",
        "direction": "neutral",
        "key_signals": ["明显楔形三推结构"],
        "detected_patterns": [],
    }
    errs = validate_detected_patterns_vs_key_signals(s1)
    assert any("wedge" in e for e in errs)
    coherence = validate_stage1_coherence(s1, strict_bar_features=False)
    assert any("wedge" in e for e in coherence)


def test_tr_boundary_syncs_middle_range_and_barbwire() -> None:
    s1 = {
        "cycle_position": "trending_tr",
        "direction": "bullish",
        "detected_patterns": [],
        "key_signals": ["价格逼近区间下沿"],
        "bar_analysis": {"entry_setup_type": "tr_boundary"},
        "program_features": {"barbwire_candidate": True, "barbwire_score": 0.72},
    }
    from pa_agent.ai.pattern_routing import sync_detected_patterns_field
    from pa_agent.ai.stage1_normalizer import normalize_stage1

    out = normalize_stage1(s1, normalization_mode="strict")
    tags = set(out["detected_patterns"])
    assert "middle_range" in tags
    assert "barbwire" in tags
    files = route_strategy_files(out)
    assert "文件21-铁丝网与无交易环境.txt" in files


def test_route_loads_spike_files_for_micro_channel_active() -> None:
    s1 = {
        "cycle_position": "micro_channel",
        "direction": "bullish",
        "spike_stage": "active",
        "detected_patterns": [],
    }
    files = route_strategy_files(s1)
    assert "极速上涨分析识别.txt" in files
    assert "极速上涨交易策略.txt" in files
    assert "上涨通道分析识别.txt" in files


def test_ema_gap_count_does_not_trigger_hl_count_setup() -> None:
    """「EMA缺口计数」must not be mistaken for H1/H2/L1/L2 count entry."""
    s1 = {
        "key_signals": [
            "连续19根K线收盘在EMA下方（EMA缺口计数19），接近20GB极端状态",
        ],
        "detected_patterns": ["reversal_attempt"],
        "risk_warning": "",
        "bar_analysis": {"entry_setup_type": "none"},
    }
    errs = validate_detected_patterns_vs_key_signals(s1)
    assert not any("h1/h2/l1/l2" in e for e in errs)
    merged = merge_detected_patterns(s1)
    assert "reversal_attempt" in merged
    assert not {"h1", "h2", "l1", "l2"}.intersection(merged)


def test_entry_setup_wedge_requires_detected_patterns_tag() -> None:
    s1 = {
        "key_signals": [],
        "detected_patterns": [],
        "bar_analysis": {"entry_setup_type": "wedge"},
    }
    errs = validate_detected_patterns_vs_key_signals(s1)
    assert any("entry_setup_type=wedge" in e for e in errs)


def test_barbwire_not_synced_from_subthreshold_risk_warning() -> None:
    from pa_agent.ai.pattern_routing import ensure_detected_patterns_coherent

    s1 = {
        "cycle_position": "trading_range",
        "direction": "neutral",
        "detected_patterns": [],
        "risk_warning": "铁丝网分数0.20未达阈值，区间中部不宜追单",
        "program_features": {"barbwire_candidate": False, "barbwire_score": 0.2},
        "bar_analysis": {"entry_setup_type": "none"},
    }
    ensure_detected_patterns_coherent(s1)
    assert "barbwire" not in s1["detected_patterns"]


def test_tr_boundary_skips_barbwire_when_not_candidate() -> None:
    from pa_agent.ai.pattern_routing import ensure_detected_patterns_coherent

    s1 = {
        "cycle_position": "trending_tr",
        "direction": "bullish",
        "detected_patterns": [],
        "bar_analysis": {"entry_setup_type": "tr_boundary"},
        "program_features": {"barbwire_candidate": False, "barbwire_score": 0.2},
    }
    ensure_detected_patterns_coherent(s1)
    tags = set(s1["detected_patterns"])
    assert "middle_range" in tags
    assert "barbwire" not in tags
