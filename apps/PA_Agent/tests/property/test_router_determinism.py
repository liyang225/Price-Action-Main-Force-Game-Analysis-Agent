"""Property-based tests for route_strategy_files determinism (task 7.4 / PR2)."""
from __future__ import annotations

import copy
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st
from pa_agent.ai.router import route_strategy_files

_CYCLE_POSITIONS = [
    "spike", "micro_channel", "tight_channel", "normal_channel", "broad_channel",
    "trending_tr", "trading_range", "extreme_tr", "unknown",
]
_DIRECTIONS = ["bullish", "bearish", "neutral"]
_PATTERNS = [
    "wedge",
    "reversal_attempt",
    "breakout_failure",
    "20gb",
    "barbwire",
    "failed_signal",
]

_ALL_VALID_FILES = frozenset([
    "提示词大纲_人设与思维方式.txt",
    "市场诊断框架.txt",
    "文件16-K线信号识别.txt",
    "文件17-止损和止盈与仓位管理.txt",
    "文件23-MeasuredMove与结构目标.txt",
    "上涨通道分析识别.txt",
    "上涨通道交易策略.txt",
    "文件13-窄通道与宽通道策略.txt",
    "下跌通道分析识别.txt",
    "下跌通道交易策略.txt",
    "极速上涨分析识别.txt",
    "极速上涨交易策略.txt",
    "极速下跌分析识别.txt",
    "极速下跌交易策略.txt",
    "震荡区间分析识别.txt",
    "震荡区间交易策略.txt",
    "文件14-楔形形态分析交易.txt",
    "文件15-二次入场机会.txt",
    "文件18-突破失败与突破测试.txt",
    "文件19-H1H2-L1L2计数.txt",
    "文件20-AlwaysIn与20GB.txt",
    "文件21-铁丝网与无交易环境.txt",
    "文件22-信号失败后的磁力位.txt",
    "文件24-最终旗形与趋势末端.txt",
    "文件25-主要趋势反转MTR.txt",
    "文件27-三角形与收敛形态.txt",
    "文件28-双重顶底与微型结构.txt",
])


def _make_stage1(cp: str, direction: str, patterns: list[str]) -> dict:
    return {
        "cycle_position": cp,
        "direction": direction,
        "detected_patterns": patterns,
    }


@given(
    cp=st.sampled_from(_CYCLE_POSITIONS),
    direction=st.sampled_from(_DIRECTIONS),
    patterns=st.lists(st.sampled_from(_PATTERNS), max_size=2, unique=True),
)
@h_settings(max_examples=300)
def test_router_deterministic(cp: str, direction: str, patterns: list[str]) -> None:
    """route_strategy_files returns the same result for the same input.

    **Validates: Requirements PR2.1**
    """
    s = _make_stage1(cp, direction, patterns)
    r1 = route_strategy_files(s)
    r2 = route_strategy_files(copy.deepcopy(s))
    assert r1 == r2, f"Non-deterministic: {r1} != {r2}"


@given(
    cp=st.sampled_from(_CYCLE_POSITIONS),
    direction=st.sampled_from(_DIRECTIONS),
    patterns=st.lists(st.sampled_from(_PATTERNS), max_size=2, unique=True),
)
@h_settings(max_examples=300)
def test_router_files_in_valid_set(cp: str, direction: str, patterns: list[str]) -> None:
    """All returned files are in the 17-file valid set.

    **Validates: Requirements PR2.1**
    """
    s = _make_stage1(cp, direction, patterns)
    result = route_strategy_files(s)
    for f in result:
        assert f in _ALL_VALID_FILES, f"Unknown file returned: {f!r}"


@given(
    cp=st.sampled_from(_CYCLE_POSITIONS),
    direction=st.sampled_from(_DIRECTIONS),
    patterns=st.lists(st.sampled_from(_PATTERNS), max_size=2, unique=True),
)
@h_settings(max_examples=300)
def test_router_stable_dedup(cp: str, direction: str, patterns: list[str]) -> None:
    """Returned list has no duplicates.

    **Validates: Requirements PR2.1**
    """
    s = _make_stage1(cp, direction, patterns)
    result = route_strategy_files(s)
    assert len(result) == len(set(result)), f"Duplicates found: {result}"


def test_router_spike_transitioning_uses_channel_files() -> None:
    result = route_strategy_files(
        {
            "cycle_position": "spike",
            "direction": "bullish",
            "spike_stage": "transitioning",
            "detected_patterns": [],
        }
    )
    assert result == [
        "上涨通道分析识别.txt",
        "上涨通道交易策略.txt",
        "文件13-窄通道与宽通道策略.txt",
    ]


def test_router_alternative_cycle_position_adds_secondary_files() -> None:
    result = route_strategy_files(
        {
            "cycle_position": "normal_channel",
            "alternative_cycle_position": "trading_range",
            "direction": "bearish",
            "detected_patterns": ["mtr"],
        }
    )
    assert result == [
        "下跌通道分析识别.txt",
        "下跌通道交易策略.txt",
        "文件13-窄通道与宽通道策略.txt",
        "震荡区间分析识别.txt",
        "震荡区间交易策略.txt",
        "文件15-二次入场机会.txt",
        "文件25-主要趋势反转MTR.txt",
        "文件19-H1H2-L1L2计数.txt",
    ]


def test_router_pattern_overlays_load_bar_by_bar_modules() -> None:
    result = route_strategy_files(
        {
            "cycle_position": "trading_range",
            "direction": "neutral",
            "detected_patterns": ["breakout_failure", "20gb", "barbwire", "failed_signal"],
        }
    )
    assert "文件18-突破失败与突破测试.txt" in result
    assert "文件20-AlwaysIn与20GB.txt" in result
    assert "文件21-铁丝网与无交易环境.txt" in result
    assert "文件22-信号失败后的磁力位.txt" in result


def test_router_neutral_channel_loads_range_for_boundary_setups() -> None:
    """Neutral wide/normal channel should get range files for boundary limit orders."""
    result = route_strategy_files(
        {
            "cycle_position": "broad_channel",
            "direction": "neutral",
            "detected_patterns": [],
        }
    )
    assert result == [
        "震荡区间分析识别.txt",
        "震荡区间交易策略.txt",
        "文件13-窄通道与宽通道策略.txt",
        "文件15-二次入场机会.txt",
        "文件19-H1H2-L1L2计数.txt",
    ]
