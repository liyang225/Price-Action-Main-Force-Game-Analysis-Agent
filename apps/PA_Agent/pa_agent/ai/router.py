"""Strategy file router — maps Stage 1 diagnosis to strategy file list.

Implements 使用说明 §11 routing table exactly.
This is a pure function: no side effects, no external state.
"""
from __future__ import annotations

import logging
from typing import Any

from pa_agent.ai.pattern_routing import merge_detected_patterns

logger = logging.getLogger(__name__)

# ── File name constants ────────────────────────────────────────────────────────

_BULLISH_CHANNEL_FILES = [
    "上涨通道分析识别.txt",
    "上涨通道交易策略.txt",
]
_BEARISH_CHANNEL_FILES = [
    "下跌通道分析识别.txt",
    "下跌通道交易策略.txt",
]
_CHANNEL_WIDTH_FILE = "文件13-窄通道与宽通道策略.txt"

_BULLISH_SPIKE_FILES = [
    "极速上涨分析识别.txt",
    "极速上涨交易策略.txt",
]
_BEARISH_SPIKE_FILES = [
    "极速下跌分析识别.txt",
    "极速下跌交易策略.txt",
]

_RANGE_FILES = [
    "震荡区间分析识别.txt",
    "震荡区间交易策略.txt",
]

_WEDGE_FILE = "文件14-楔形形态分析交易.txt"
_REVERSAL_FILE = "文件15-二次入场机会.txt"
_BREAKOUT_FAILURE_FILE = "文件18-突破失败与突破测试.txt"
_H1H2_FILE = "文件19-H1H2-L1L2计数.txt"
_ALWAYS_IN_FILE = "文件20-AlwaysIn与20GB.txt"
_BARBWIRE_FILE = "文件21-铁丝网与无交易环境.txt"
_MAGNET_FILE = "文件22-信号失败后的磁力位.txt"
_MTR_FILE = "文件25-主要趋势反转MTR.txt"
_FINAL_FLAG_FILE = "文件24-最终旗形与趋势末端.txt"
_TRIANGLE_FILE = "文件27-三角形与收敛形态.txt"
_DOUBLE_TOP_BOTTOM_FILE = "文件28-双重顶底与微型结构.txt"

# All valid file names (used for dedup validation)
_ALL_VALID_FILES: frozenset[str] = frozenset([
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

_CHANNEL_STATES = frozenset(["micro_channel", "tight_channel", "normal_channel", "broad_channel"])
_RANGE_STATES = frozenset(["trading_range", "trending_tr"])
_SKIP_STATES = frozenset(["extreme_tr", "unknown"])


def route_strategy_files(stage1_json: dict[str, Any]) -> list[str]:
    """Return the ordered, deduplicated list of strategy files for Stage 2.

    Args:
        stage1_json: The validated Stage 1 diagnosis JSON object.

    Returns:
        List of file names to load, in the order they should appear in the
        Stage 2 system prompt. Always a subset of the known prompt files.
        Empty list means "do not trade" (extreme_tr / unknown).
    """
    cp = stage1_json.get("cycle_position", "unknown")
    direction = stage1_json.get("direction", "neutral")
    patterns = merge_detected_patterns(stage1_json)
    spike_stage = stage1_json.get("spike_stage")
    alternative_cp = stage1_json.get("alternative_cycle_position")

    files: list[str] = []
    files.extend(_base_files_for_cycle(cp, direction, spike_stage=spike_stage))

    # Brooks: near-term spike is trading core even when cycle_position is channel/range
    tc = stage1_json.get("trend_context") or {}
    recent_spike = tc.get("recent_spike") if isinstance(tc, dict) else None
    if recent_spike == "bullish" and cp != "spike" and direction == "bullish":
        files.extend(_BULLISH_SPIKE_FILES)
    elif recent_spike == "bearish" and cp != "spike" and direction == "bearish":
        files.extend(_BEARISH_SPIKE_FILES)

    if alternative_cp and alternative_cp != cp:
        files.extend(_base_files_for_cycle(str(alternative_cp), direction, spike_stage=None))

    # ── Pattern overlays ──────────────────────────────────────────────────────
    if "wedge" in patterns:
        files.append(_WEDGE_FILE)
    if (
        cp in _CHANNEL_STATES
        or "reversal_attempt" in patterns
        or "mtr" in patterns
        or "final_flag" in patterns
        or "h2" in patterns
        or "l2" in patterns
    ):
        files.append(_REVERSAL_FILE)
    if "mtr" in patterns:
        files.append(_MTR_FILE)
    if "final_flag" in patterns:
        files.append(_FINAL_FLAG_FILE)
    if cp in _CHANNEL_STATES or any(p in patterns for p in ("h1", "h2", "l1", "l2")):
        files.append(_H1H2_FILE)
    if any(
        p in patterns
        for p in ("breakout_failure", "failed_breakout", "breakout_test", "breakout_pullback")
    ):
        files.append(_BREAKOUT_FAILURE_FILE)
    if any(p in patterns for p in ("always_in", "ail", "ais", "20gb", "gap_bar")):
        files.append(_ALWAYS_IN_FILE)
    if cp in _RANGE_STATES or any(p in patterns for p in ("barbwire", "wire", "overlap", "middle_range")):
        files.append(_BARBWIRE_FILE)
    if any(
        p in patterns
        for p in ("failed_signal", "breakout_failure", "failed_breakout", "magnet", "trapped_traders")
    ):
        files.append(_MAGNET_FILE)
    if any(
        p in patterns
        for p in (
            "ascending_triangle",
            "descending_triangle",
            "symmetrical_triangle",
            "expanding_triangle",
        )
    ):
        files.append(_TRIANGLE_FILE)
    if "double_top_bottom" in patterns:
        files.append(_DOUBLE_TOP_BOTTOM_FILE)

    # ── Stable dedup (preserve first occurrence) ──────────────────────────────
    seen: set[str] = set()
    deduped: list[str] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            deduped.append(f)

    return deduped


def _base_files_for_cycle(
    cp: str,
    direction: str,
    *,
    spike_stage: Any = None,
) -> list[str]:
    """Return base strategy files before pattern overlays."""
    files: list[str] = []

    # spike transitioning is already behaving like a channel; ending keeps spike
    # context but preloads channel rules for the likely spike-and-channel shift.
    if cp == "spike" and spike_stage == "transitioning":
        return _channel_files(direction)

    # ── Channel states ────────────────────────────────────────────────────────
    if cp in _CHANNEL_STATES:
        files.extend(_channel_files(direction))
        # micro_channel is often spike on the signal window; load spike playbooks when active/ending.
        if cp == "micro_channel" and spike_stage in ("active", "ending"):
            if direction == "bullish":
                files.extend(_BULLISH_SPIKE_FILES)
            elif direction == "bearish":
                files.extend(_BEARISH_SPIKE_FILES)

    # ── Spike state ───────────────────────────────────────────────────────────
    elif cp == "spike":
        if direction == "bullish":
            files.extend(_BULLISH_SPIKE_FILES)
        elif direction == "bearish":
            files.extend(_BEARISH_SPIKE_FILES)
        else:
            logger.info("Spike with neutral direction — no spike strategy files loaded")
        if spike_stage == "ending":
            files.extend(_channel_files(direction))

    # ── Range states ──────────────────────────────────────────────────────────
    elif cp in _RANGE_STATES:
        files.extend(_RANGE_FILES)

    # ── Skip states (extreme_tr / unknown) ────────────────────────────────────
    elif cp in _SKIP_STATES:
        pass  # no strategy files — do not trade

    else:
        logger.warning(
            "Unknown cycle_position %r — no strategy files loaded. "
            "If this is a pattern name (e.g. 'descending_triangle'), it belongs in "
            "detected_patterns, not cycle_position. "
            "Run normalize_stage1() before route_strategy_files() to auto-correct.",
            cp,
        )

    return files


def _channel_files(direction: str) -> list[str]:
    files: list[str] = []
    if direction == "bullish":
        files.extend(_BULLISH_CHANNEL_FILES)
    elif direction == "bearish":
        files.extend(_BEARISH_CHANNEL_FILES)
    else:
        # Neutral in a channel: skip directional channel files, but preload
        # range strategy for boundary planned-limit setups (§9.0 path).
        logger.info(
            "Channel-like state with neutral direction — "
            "no directional channel files; loading range strategy for boundary setups"
        )
        files.extend(_RANGE_FILES)
    files.append(_CHANNEL_WIDTH_FILE)
    return files
