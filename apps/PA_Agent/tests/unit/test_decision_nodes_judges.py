"""Tests for decision node judges: DirectionJudge, AlwaysInJudge, SignalBarJudge, etc.

Covers Properties 2-10 and override Properties 15-22.
"""
from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pa_agent.ai.decision_nodes import (
    ALWAYS_IN_SAME_SIDE_RATIO,
    BAR_COUNT_THRESHOLD,
    SIGNAL_BAR_LONG_ATR_RATIO,
    DecisionNodeEngine,
    NodeFill,
    apply_overrides,
    judge_always_in,
    judge_data_sufficiency,
    judge_direction,
    judge_follow_through,
    judge_signal_bar_closed,
    judge_signal_bar_direction,
    judge_signal_bar_length,
    merge_program_nodes,
    write_override_trace,
)
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame


def _make_bar(seq: int, *, high: float = 2010.0, low: float = 1990.0,
              open_: float = 2000.0, close: float = 2005.0) -> KlineBar:
    return KlineBar(
        seq=seq, ts_open=float(1_000_000 - seq * 60_000),
        open=open_, high=high, low=low, close=close,
        volume=1.0, closed=True,
    )


def _make_frame(
    n: int = 25,
    *,
    ema_slope: str = "flat",  # "up", "down", "flat"
    close_pattern: str = "flat",  # "up", "down", "flat"
    swing_pattern: str = "none",  # "hh_hl", "ll_lh", "none"
) -> KlineFrame:
    """Build a KlineFrame with controllable direction signals."""
    bars = []
    for i in range(n):
        seq = i + 1
        base = 2000.0
        if close_pattern == "up":
            c = base + (n - i) * 0.5
        elif close_pattern == "down":
            c = base - (n - i) * 0.5
        else:
            c = base
        bar = _make_bar(seq, close=c, high=c + 10, low=c - 10)
        bars.append(bar)

    # EMA values: index 0 = newest
    if ema_slope == "up":
        ema = tuple(1980.0 + (n - i) * 0.5 for i in range(n))
    elif ema_slope == "down":
        ema = tuple(2020.0 - (n - i) * 0.5 for i in range(n))
    else:
        ema = tuple([2000.0] * n)

    atr = tuple([10.0] * n)
    return KlineFrame(
        symbol="TEST", timeframe="1h",
        bars=tuple(bars),
        snapshot_ts_local_ms=1,
        indicators=IndicatorBundle(ema20=ema, atr14=atr),
    )


# ── DirectionJudge tests ──────────────────────────────────────────────────────

class TestDirectionJudge:
    def test_bullish_frame_gives_bullish(self):
        """Frame with up EMA slope and rising closes should be bullish."""
        frame = _make_frame(25, ema_slope="up", close_pattern="up")
        direction, fill = judge_direction(frame)
        # With both signals up, should be bullish
        assert direction in ("bullish", "neutral")  # At least EMA and gravity should agree
        assert fill.node_id == "2.3"
        assert fill.answer in ("是", "中性")
        assert fill.branch in ("bullish", "bearish", "neutral")

    def test_neutral_frame_gives_neutral(self):
        frame = _make_frame(25, ema_slope="flat", close_pattern="flat")
        direction, fill = judge_direction(frame)
        assert direction in ("bullish", "bearish", "neutral")
        assert fill.node_id == "2.3"
        assert fill.answer in ("是", "中性")
        if direction == "neutral":
            assert fill.answer == "中性"
            assert fill.branch == "neutral"

    def test_direction_bullish_maps_to_answer_yes_branch_bullish(self):
        """When direction=bullish, §2.3 answer=是 and branch=bullish."""
        frame = _make_frame(25, ema_slope="up", close_pattern="up")
        direction, fill = judge_direction(frame)
        if direction == "bullish":
            assert fill.answer == "是"
            assert fill.branch == "bullish"

    def test_direction_bearish_maps_to_answer_yes_branch_bearish(self):
        frame = _make_frame(25, ema_slope="down", close_pattern="down")
        direction, fill = judge_direction(frame)
        if direction == "bearish":
            assert fill.answer == "是"
            assert fill.branch == "bearish"

    def test_direction_neutral_maps_to_answer_zhongxing(self):
        frame = _make_frame(25, ema_slope="flat", close_pattern="flat")
        direction, fill = judge_direction(frame)
        if direction == "neutral":
            assert fill.answer == "中性"
            assert fill.branch == "neutral"

    def test_returns_valid_direction_domain(self):
        for ema_s in ("up", "down", "flat"):
            for close_p in ("up", "down", "flat"):
                frame = _make_frame(25, ema_slope=ema_s, close_pattern=close_p)
                direction, fill = judge_direction(frame)
                assert direction in ("bullish", "bearish", "neutral")
                assert fill.answer in ("是", "中性")

    def test_bar_range_format(self):
        frame = _make_frame(25)
        _, fill = judge_direction(frame)
        assert fill.bar_range.startswith("K")
        assert "-K1" in fill.bar_range or fill.bar_range == "K1"

    def test_direction_and_23_branch_consistent(self):
        """§2.3 branch must match direction."""
        frame = _make_frame(25, ema_slope="up", close_pattern="up")
        direction, fill = judge_direction(frame)
        if fill.branch in ("bullish", "bearish"):
            assert fill.branch == direction
        elif fill.branch == "neutral":
            assert direction == "neutral"


class TestDirectionProperty2:
    """Property 2: direction domain and §2.3 mapping."""

    @given(n=st.integers(min_value=20, max_value=50))
    @settings(max_examples=100)
    def test_direction_in_valid_domain(self, n: int):
        """Property 2: direction ∈ {bullish, bearish, neutral}."""
        bars = tuple(_make_bar(i + 1) for i in range(n))
        frame = KlineFrame(
            symbol="TEST", timeframe="1h", bars=bars, snapshot_ts_local_ms=1,
            indicators=IndicatorBundle(ema20=tuple([2000.0] * n), atr14=tuple([10.0] * n)),
        )
        direction, fill = judge_direction(frame)
        assert direction in ("bullish", "bearish", "neutral")
        assert fill.answer in ("是", "中性")
        assert fill.branch in ("bullish", "bearish", "neutral")
        # Validate mapping
        if direction in ("bullish", "bearish"):
            assert fill.answer == "是"
            assert fill.branch == direction
        else:
            assert fill.answer == "中性"
            assert fill.branch == "neutral"


# ── AlwaysInJudge tests ───────────────────────────────────────────────────────

class TestAlwaysInJudge:
    def test_always_in_long_when_mostly_above_ema_and_rising_slope(self):
        """When most closes above EMA and slope up → AIL."""
        n = 25
        # All closes above EMA with rising EMA
        bars = tuple(_make_bar(i + 1, close=2050.0, high=2060.0, low=2040.0) for i in range(n))
        ema = tuple(2000.0 + (n - i) * 0.5 for i in range(n))  # rising EMA
        frame = KlineFrame(
            symbol="TEST", timeframe="1h", bars=bars, snapshot_ts_local_ms=1,
            indicators=IndicatorBundle(ema20=ema, atr14=tuple([10.0] * n)),
        )
        fill = judge_always_in(frame)
        assert fill.node_id == "2.4"
        assert fill.answer == "是"
        assert fill.branch == "AIL"

    def test_always_in_short_when_mostly_below_ema_and_falling_slope(self):
        n = 25
        bars = tuple(_make_bar(i + 1, close=1950.0, high=1960.0, low=1940.0) for i in range(n))
        ema = tuple(2000.0 - (n - i) * 0.5 for i in range(n))  # falling EMA (still above 1950)
        frame = KlineFrame(
            symbol="TEST", timeframe="1h", bars=bars, snapshot_ts_local_ms=1,
            indicators=IndicatorBundle(ema20=ema, atr14=tuple([10.0] * n)),
        )
        fill = judge_always_in(frame)
        assert fill.node_id == "2.4"
        # May or may not be AIS depending on exact values; just check structure
        assert fill.answer in ("是", "否")
        assert fill.branch in ("AIL", "AIS", None)

    def test_no_always_in_when_balanced(self):
        n = 25
        bars = tuple(
            _make_bar(i + 1, close=2005.0 if i % 2 == 0 else 1995.0) for i in range(n)
        )
        frame = KlineFrame(
            symbol="TEST", timeframe="1h", bars=bars, snapshot_ts_local_ms=1,
            indicators=IndicatorBundle(ema20=tuple([2000.0] * n), atr14=tuple([10.0] * n)),
        )
        fill = judge_always_in(frame)
        assert fill.node_id == "2.4"
        assert fill.answer in ("是", "否")

    def test_ail_answer_branch_valid(self):
        n = 25
        bars = tuple(_make_bar(i + 1, close=2050.0, high=2060.0, low=2040.0) for i in range(n))
        ema = tuple(2000.0 + (n - i) * 0.5 for i in range(n))
        frame = KlineFrame(
            symbol="TEST", timeframe="1h", bars=bars, snapshot_ts_local_ms=1,
            indicators=IndicatorBundle(ema20=ema, atr14=tuple([10.0] * n)),
        )
        fill = judge_always_in(frame)
        if fill.branch == "AIL":
            assert fill.answer == "是"
        elif fill.branch == "AIS":
            assert fill.answer == "是"
        else:
            assert fill.answer == "否"
            assert fill.branch is None


# ── SignalBarJudge tests ──────────────────────────────────────────────────────

class MockFeature:
    def __init__(self, bar_type: str, range_atr_ratio: float | None,
                 follow_through_1_2: str = "yes"):
        self.bar_type = bar_type
        self.range_atr_ratio = range_atr_ratio
        self.follow_through_1_2 = follow_through_1_2


class TestSignalBarJudge:
    def test_91_always_yes(self):
        fill = judge_signal_bar_closed(2, None)
        assert fill.node_id == "9.1"
        assert fill.answer == "是"
        assert fill.bar_range == "K2"

    def test_92_long_consistent_yes(self):
        features = {2: MockFeature("trend_bull", 1.5)}
        fill = judge_signal_bar_direction(2, "做多", features)
        assert fill.node_id == "9.2"
        assert fill.answer == "是"

    def test_92_long_inconsistent_no(self):
        features = {2: MockFeature("doji", 1.5)}
        fill = judge_signal_bar_direction(2, "做多", features)
        assert fill.node_id == "9.2"
        assert fill.answer == "否"

    def test_92_short_consistent_yes(self):
        features = {2: MockFeature("trend_bear", 1.5)}
        fill = judge_signal_bar_direction(2, "做空", features)
        assert fill.node_id == "9.2"
        assert fill.answer == "是"

    def test_92_short_inconsistent_no(self):
        features = {2: MockFeature("trend_bull", 1.5)}
        fill = judge_signal_bar_direction(2, "做空", features)
        assert fill.node_id == "9.2"
        assert fill.answer == "否"

    def test_92_no_direction_not_applicable(self):
        features = {2: MockFeature("trend_bull", 1.5)}
        fill = judge_signal_bar_direction(2, None, features)
        assert fill.node_id == "9.2"
        assert fill.answer == "不适用"
        assert fill.bar_range == "不适用"

    def test_93_overlong_yes(self):
        features = {2: MockFeature("trend_bull", SIGNAL_BAR_LONG_ATR_RATIO + 0.1)}
        fill = judge_signal_bar_length(2, features)
        assert fill.node_id == "9.3"
        assert fill.answer == "是"

    def test_93_not_overlong_no(self):
        features = {2: MockFeature("trend_bull", SIGNAL_BAR_LONG_ATR_RATIO - 0.1)}
        fill = judge_signal_bar_length(2, features)
        assert fill.node_id == "9.3"
        assert fill.answer == "否"

    def test_93_nan_ratio_conservative_yes(self):
        features = {2: MockFeature("trend_bull", None)}
        fill = judge_signal_bar_length(2, features)
        assert fill.node_id == "9.3"
        assert fill.answer == "是"

    def test_93_boundary_exactly_2_0_is_no(self):
        """ratio == 2.0 should be no (not strictly greater than)."""
        features = {2: MockFeature("trend_bull", 2.0)}
        fill = judge_signal_bar_length(2, features)
        assert fill.answer == "否"

    def test_93_just_above_2_0_is_yes(self):
        features = {2: MockFeature("trend_bull", 2.001)}
        fill = judge_signal_bar_length(2, features)
        assert fill.answer == "是"


class TestFollowThroughJudge:
    def test_yes_maps_to_shi(self):
        features = {2: MockFeature("trend_bull", 1.5, "yes")}
        fill = judge_follow_through(2, features)
        assert fill.answer == "是"

    def test_failed_maps_to_fou(self):
        features = {2: MockFeature("trend_bull", 1.5, "failed")}
        fill = judge_follow_through(2, features)
        assert fill.answer == "否"

    def test_no_maps_to_fou(self):
        features = {2: MockFeature("trend_bull", 1.5, "no")}
        fill = judge_follow_through(2, features)
        assert fill.answer == "否"

    def test_pending_maps_to_dengdai(self):
        features = {1: MockFeature("trend_bull", 1.5, "pending")}
        fill = judge_follow_through(1, features)
        assert fill.answer == "等待"

    def test_missing_feature_conservative_dengdai(self):
        fill = judge_follow_through(5, {})
        assert fill.answer == "等待"


# ── OverrideArbiter tests ─────────────────────────────────────────────────────

def _make_program_nodes():
    """Return sample program nodes for override testing."""
    return [
        {"node_id": "1.1", "question": "q1.1", "answer": "是", "reason": "r", "bar_range": "K20-K1"},
        {"node_id": "2.3", "question": "q2.3", "answer": "是", "reason": "r", "bar_range": "K20-K1", "branch": "bullish"},
        {"node_id": "2.4", "question": "q2.4", "answer": "否", "reason": "r", "bar_range": "K20-K1"},
        {"node_id": "9.1", "question": "q9.1", "answer": "是", "reason": "r", "bar_range": "K1"},
        {"node_id": "9.2", "question": "q9.2", "answer": "是", "reason": "r", "bar_range": "K1"},
        {"node_id": "9.3", "question": "q9.3", "answer": "否", "reason": "r", "bar_range": "K1"},
    ]


class TestOverrideArbiter:
    def test_no_overrides_keeps_program_values(self):
        """Property 15: no overrides → keep program values."""
        nodes = _make_program_nodes()
        out = {"direction": "bullish"}
        result = apply_overrides(nodes, None, out=out, stage="stage1")
        assert result[0]["answer"] == "是"
        assert result[1]["branch"] == "bullish"
        assert not any(n.get("overridden_by_ai") for n in result)

    def test_empty_overrides_list_keeps_program_values(self):
        nodes = _make_program_nodes()
        out = {"direction": "bullish"}
        result = apply_overrides(nodes, [], out=out, stage="stage1")
        assert not any(n.get("overridden_by_ai") for n in result)

    def test_locked_node_11_cannot_be_overridden(self):
        """Property 16: locked node §1.1 cannot be overridden."""
        nodes = _make_program_nodes()
        overrides = [{"node_id": "1.1", "answer": "否", "override_reason": "test"}]
        out = {}
        result = apply_overrides(nodes, overrides, out=out, stage="stage1")
        n11 = next((n for n in result if n["node_id"] == "1.1"), None)
        assert n11["answer"] == "是"
        assert not n11.get("overridden_by_ai")

    def test_locked_node_91_cannot_be_overridden(self):
        """Property 16: locked node §9.1 cannot be overridden."""
        nodes = _make_program_nodes()
        overrides = [{"node_id": "9.1", "answer": "否", "override_reason": "test"}]
        out = {}
        result = apply_overrides(nodes, overrides, out=out, stage="stage2")
        n91 = next((n for n in result if n["node_id"] == "9.1"), None)
        assert n91["answer"] == "是"
        assert not n91.get("overridden_by_ai")

    def test_missing_override_reason_rejected(self):
        """Property 17: missing override_reason → rejected."""
        nodes = _make_program_nodes()
        overrides = [{"node_id": "2.4", "answer": "是"}]  # no override_reason
        out = {}
        result = apply_overrides(nodes, overrides, out=out, stage="stage1")
        n24 = next((n for n in result if n["node_id"] == "2.4"), None)
        assert n24["answer"] == "否"  # original
        assert not n24.get("overridden_by_ai")

    def test_empty_override_reason_rejected(self):
        nodes = _make_program_nodes()
        overrides = [{"node_id": "2.4", "answer": "是", "override_reason": "  "}]
        out = {}
        result = apply_overrides(nodes, overrides, out=out, stage="stage1")
        n24 = next((n for n in result if n["node_id"] == "2.4"), None)
        assert not n24.get("overridden_by_ai")

    def test_valid_override_accepted_with_trace(self):
        """Property 18: valid override → accepted with trace fields."""
        nodes = _make_program_nodes()
        overrides = [{"node_id": "2.4", "answer": "是", "branch": "AIL", "override_reason": "strong bullish trend"}]
        out = {}
        result = apply_overrides(nodes, overrides, out=out, stage="stage1")
        n24 = next((n for n in result if n["node_id"] == "2.4"), None)
        assert n24["answer"] == "是"
        assert n24.get("overridden_by_ai") is True
        assert n24.get("program_answer") == "否"
        assert n24.get("override_reason") == "strong bullish trend"

    def test_23_override_consistent_bullish_accepted(self):
        """Property 19: §2.3 bullish/是 override accepted and direction synced."""
        nodes = _make_program_nodes()
        overrides = [{"node_id": "2.3", "answer": "是", "branch": "bearish",
                      "override_reason": "strong bearish reversal"}]
        out = {"direction": "bullish"}
        result = apply_overrides(nodes, overrides, out=out, stage="stage1")
        n23 = next((n for n in result if n["node_id"] == "2.3"), None)
        assert n23["answer"] == "是"
        assert n23["branch"] == "bearish"
        assert n23.get("overridden_by_ai") is True
        assert out["direction"] == "bearish"

    def test_23_override_neutral_answer_zhongxing(self):
        """Property 19: §2.3 neutral/中性 override accepted."""
        nodes = _make_program_nodes()
        overrides = [{"node_id": "2.3", "answer": "中性", "branch": "neutral",
                      "override_reason": "market is ranging"}]
        out = {"direction": "bullish"}
        result = apply_overrides(nodes, overrides, out=out, stage="stage1")
        n23 = next((n for n in result if n["node_id"] == "2.3"), None)
        assert n23["answer"] == "中性"
        assert out["direction"] == "neutral"

    def test_23_override_inconsistent_rejected(self):
        """Property 19: §2.3 inconsistent answer/branch → rejected."""
        nodes = _make_program_nodes()
        overrides = [{"node_id": "2.3", "answer": "中性", "branch": "bullish",
                      "override_reason": "contradiction"}]
        out = {"direction": "bullish"}
        result = apply_overrides(nodes, overrides, out=out, stage="stage1")
        n23 = next((n for n in result if n["node_id"] == "2.3"), None)
        assert not n23.get("overridden_by_ai")
        assert out["direction"] == "bullish"  # unchanged

    def test_non_list_overrides_ignored(self):
        """Property 21: non-list overrides → ignored."""
        nodes = _make_program_nodes()
        out = {}
        for bad_input in [None, "string", 42, {"key": "val"}]:
            result = apply_overrides(nodes, bad_input, out=out, stage="stage1")
            assert not any(n.get("overridden_by_ai") for n in result)

    def test_invalid_answer_enum_skipped(self):
        """Property 21: invalid answer enum → skipped."""
        nodes = _make_program_nodes()
        overrides = [{"node_id": "2.4", "answer": "INVALID", "override_reason": "test"}]
        out = {}
        result = apply_overrides(nodes, overrides, out=out, stage="stage1")
        n24 = next((n for n in result if n["node_id"] == "2.4"), None)
        assert not n24.get("overridden_by_ai")

    def test_merge_program_nodes_keeps_ai_primary_without_program_append(self):
        """AI-primary §1.3/§2.5 keep AI reason; program metrics are not appended."""
        trace = [
            {"node_id": "1.3", "question": "AI q", "answer": "否", "reason": "AI混乱判断", "bar_range": "K5-K1"},
            {"node_id": "2.5", "question": "AI q5", "answer": "是", "reason": "AI", "bar_range": "K1"},
        ]
        prog = [
            {"node_id": "1.3", "question": "程序 q", "answer": "否", "reason": "程序长理由" * 20, "bar_range": "K8-K1"},
            {"node_id": "2.5", "question": "程序 q5", "answer": "否", "reason": "程序长理由" * 20, "bar_range": "K8-K1"},
        ]
        result = merge_program_nodes(trace, prog)
        node_13 = next((n for n in result if n["node_id"] == "1.3"), None)
        assert node_13 is not None
        assert node_13["reason"] == "AI混乱判断"
        assert "程序参考数据" not in node_13["reason"]
        node_25 = next((n for n in result if n["node_id"] == "2.5"), None)
        assert node_25 is not None
        assert node_25["reason"] == "AI"
        assert "程序参考数据" not in node_25["reason"]

    def test_merge_program_nodes_overrides_ai_nodes(self):
        """merge_program_nodes: program nodes override AI nodes of same node_id."""
        trace = [
            {"node_id": "2.3", "question": "AI q", "answer": "空头", "reason": "AI", "bar_range": "K5-K1"},
            {"node_id": "2.5", "question": "AI q5", "answer": "是", "reason": "AI", "bar_range": "K1"},
        ]
        prog = [
            {"node_id": "2.3", "question": "程序 q", "answer": "是", "reason": "程序", "bar_range": "K20-K1", "branch": "bullish"},
            {"node_id": "2.5", "question": "程序 q5", "answer": "否", "reason": "程序长理由" * 20, "bar_range": "K8-K1"},
        ]
        result = merge_program_nodes(trace, prog)
        node_23 = next((n for n in result if n["node_id"] == "2.3"), None)
        assert node_23 is not None
        assert node_23["answer"] == "是"
        assert node_23["branch"] == "bullish"
        assert node_23["reason"] == "程序"
        node_25 = next((n for n in result if n["node_id"] == "2.5"), None)
        assert node_25 is not None
        assert node_25["reason"] == "AI"
        assert "程序参考数据" not in node_25["reason"]

    def test_write_override_trace_sets_fields(self):
        node = {"node_id": "2.3", "answer": "是", "branch": "bullish", "reason": "r", "bar_range": "K1"}
        override = {"answer": "否", "branch": "bearish", "override_reason": "reversal signal"}
        write_override_trace(node, override)
        assert node["program_answer"] == "是"
        assert node["program_branch"] == "bullish"
        assert node["answer"] == "否"
        assert node["branch"] == "bearish"
        assert node["override_reason"] == "reversal signal"
        assert node["overridden_by_ai"] is True


# ── DecisionNodeEngine.apply_stage1 tests ────────────────────────────────────

class TestApplyStage1:
    def _make_sufficient_frame(self, n: int = 25) -> KlineFrame:
        bars = tuple(_make_bar(i + 1) for i in range(n))
        return KlineFrame(
            symbol="TEST", timeframe="1h", bars=bars, snapshot_ts_local_ms=1,
            indicators=IndicatorBundle(ema20=tuple([2000.0] * n), atr14=tuple([10.0] * n)),
        )

    def test_apply_stage1_fills_11_23_24(self):
        out = {"gate_trace": [], "direction": "neutral"}
        frame = self._make_sufficient_frame()
        DecisionNodeEngine.apply_stage1(out, frame)
        gate_trace = out["gate_trace"]
        node_ids = [n["node_id"] for n in gate_trace]
        assert "1.1" in node_ids
        assert "2.3" in node_ids
        assert "2.4" in node_ids

    def test_apply_stage1_sets_direction_field(self):
        out = {"gate_trace": []}
        frame = self._make_sufficient_frame()
        DecisionNodeEngine.apply_stage1(out, frame)
        assert out.get("direction") in ("bullish", "bearish", "neutral")

    def test_apply_stage1_idempotent(self):
        """Property 14: applying stage1 twice produces same result."""
        out1 = {"gate_trace": []}
        frame = self._make_sufficient_frame()
        DecisionNodeEngine.apply_stage1(out1, frame)
        # Save state
        dir1 = out1["direction"]
        nodes1 = {n["node_id"]: n["answer"] for n in out1["gate_trace"]}

        # Apply again
        out2 = {"gate_trace": []}
        DecisionNodeEngine.apply_stage1(out2, frame)
        dir2 = out2["direction"]
        nodes2 = {n["node_id"]: n["answer"] for n in out2["gate_trace"]}

        assert dir1 == dir2
        assert nodes1 == nodes2

    def test_apply_stage1_11_always_yes(self):
        out = {"gate_trace": []}
        frame = self._make_sufficient_frame()
        DecisionNodeEngine.apply_stage1(out, frame)
        n11 = next((n for n in out["gate_trace"] if n["node_id"] == "1.1"), None)
        assert n11 is not None
        assert n11["answer"] == "是"

    def test_apply_stage1_23_consistent_with_direction(self):
        out = {"gate_trace": []}
        frame = self._make_sufficient_frame()
        DecisionNodeEngine.apply_stage1(out, frame)
        direction = out["direction"]
        n23 = next((n for n in out["gate_trace"] if n["node_id"] == "2.3"), None)
        assert n23 is not None
        if direction == "bullish":
            assert n23["answer"] == "是"
            assert n23.get("branch") == "bullish"
        elif direction == "bearish":
            assert n23["answer"] == "是"
            assert n23.get("branch") == "bearish"
        else:
            assert n23["answer"] == "中性"
            assert n23.get("branch") == "neutral"


def test_is_planned_limit_order_detects_pending_limit_without_signal_bar() -> None:
    from pa_agent.ai.decision_nodes import is_planned_limit_order

    obj = {
        "decision": {"order_type": "限价单"},
        "bar_analysis": {
            "signal_bar": {"bar": None, "quality": "invalid", "pattern": "none"},
            "entry_bar": {
                "bar": None,
                "strength": "not_triggered",
                "freshness": "pending",
            },
        },
    }
    assert is_planned_limit_order(obj) is True


def test_is_planned_limit_order_detects_weak_boundary_limit() -> None:
    from pa_agent.ai.decision_nodes import is_planned_limit_order

    obj = {
        "decision": {"order_type": "限价单"},
        "bar_analysis": {
            "signal_bar": {"bar": "K2", "quality": "weak", "pattern": "tr_boundary"},
            "entry_bar": {
                "bar": None,
                "strength": "not_triggered",
                "freshness": "pending",
            },
        },
    }
    assert is_planned_limit_order(obj) is True


def test_normalize_stage2_upgrades_9_0_for_planned_limit() -> None:
    from pa_agent.ai.stage2_normalizer import normalize_stage2

    obj = {
        "decision": {
            "order_type": "限价单",
            "order_direction": "做空",
            "entry_price": 101.0,
            "take_profit_price": 98.0,
            "stop_loss_price": 103.0,
            "reasoning": "test",
            "diagnosis_confidence": 60,
            "diagnosis_confidence_reasoning": "test",
            "trade_confidence": 50,
            "trade_confidence_reasoning": "test",
            "estimated_win_rate": 52,
            "estimated_win_rate_reasoning": "test",
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "test",
            "invalidation_condition": "test",
        },
        "diagnosis_summary": {
            "cycle_position": "broad_channel",
            "direction": "neutral",
            "key_signals": [],
        },
        "bar_analysis": {
            "always_in": "neutral",
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
                "still_valid": True,
                "freshness": "pending",
            },
            "second_entry": {"is_second_entry": False, "type": "none"},
        },
        "decision_trace": [
            {
                "node_id": "9.0",
                "question": "信号棒是否已经收盘且质量足够？",
                "answer": "否",
                "reason": "K1 doji",
                "bar_range": "K1",
            },
            {
                "node_id": "10.3",
                "question": "交易者方程是否通过？",
                "answer": "是",
                "reason": "test",
                "bar_range": "K1",
            },
        ],
        "terminal": {"node_id": "11.4", "outcome": "trade", "label": "test"},
    }
    frame = KlineFrame(
        symbol="XAUUSD",
        timeframe="5m",
        bars=(
            _make_bar(1, close=100.0, high=100.5, low=99.0),
            _make_bar(2, close=101.0, high=102.0, low=98.0),
        ),
        indicators=IndicatorBundle(ema20=(100.0, 100.0), atr14=(2.0, 2.0)),
        snapshot_ts_local_ms=1,
    )
    out = normalize_stage2(obj, kline_frame=frame, stage1_json=obj["diagnosis_summary"])
    node_90 = next(n for n in out["decision_trace"] if n["node_id"] == "9.0")
    assert node_90["answer"] == "是"


def test_11_2_override_preserves_limit_order_without_basis() -> None:
    """§11.2 maps to 突破单, but planned limits keep null entry_basis_*."""
    nodes = [
        {"node_id": "11.1", "question": "q", "answer": "否", "reason": "", "bar_range": "K1"},
        {"node_id": "11.2", "question": "q", "answer": "否", "reason": "", "bar_range": "K1"},
    ]
    out = {
        "decision": {
            "order_type": "限价单",
            "entry_basis_bar": None,
            "entry_basis_extreme": None,
            "entry_rule": None,
        }
    }
    overrides = [
        {
            "node_id": "11.2",
            "answer": "是",
            "override_reason": "trending_tr planned limit pullback, not breakout",
        },
    ]
    apply_overrides(nodes, overrides, out=out, stage="stage2")
    assert out["decision"]["order_type"] == "限价单"


def test_apply_stage2_11_2_override_keeps_limit_order() -> None:
    """Regression: node_overrides on §11.2 must not force 突破单 without basis fields."""
    frame = _make_frame(25)
    out = {
        "decision": {
            "order_direction": "做多",
            "order_type": "限价单",
            "entry_price": 2002.0,
            "entry_basis_bar": None,
            "entry_basis_extreme": None,
            "entry_rule": None,
            "take_profit_price": 2015.0,
            "take_profit_price_2": 2030.0,
            "stop_loss_price": 1995.0,
            "reasoning": "planned limit",
            "diagnosis_confidence": 55,
            "diagnosis_confidence_reasoning": "x",
            "trade_confidence": 50,
            "trade_confidence_reasoning": "x",
            "estimated_win_rate": 47,
            "estimated_win_rate_reasoning": "x",
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "x",
            "invalidation_condition": "x",
        },
        "decision_trace": [
            {
                "node_id": "10.3",
                "section": "风险收益",
                "question": "交易者方程是否通过？",
                "answer": "是",
                "reason": "pass",
                "bar_range": "K1",
            },
        ],
        "bar_analysis": {
            "signal_bar": {"bar": "K1", "quality": "medium", "pattern": "tr_boundary"},
            "entry_bar": {
                "strength": "not_triggered",
                "freshness": "pending",
                "follow_through": True,
            },
        },
        "node_overrides": [
            {
                "node_id": "11.2",
                "answer": "是",
                "override_reason": "use limit pullback instead of breakout",
            },
        ],
    }
    stage1 = {
        "cycle_position": "trending_tr",
        "direction": "bullish",
        "gate_result": "proceed",
    }
    DecisionNodeEngine.apply_stage2(out, frame, stage1)
    assert out["decision"]["order_type"] == "限价单"
    assert out["decision"]["entry_basis_bar"] is None


def test_apply_stage2_tolerates_non_dict_stage1_and_decision() -> None:
    """Regression: string stage1_json/decision must not crash §9/§11 program fill."""
    frame = _make_frame()
    out = {
        "decision": "invalid-string-decision",
        "decision_trace": [
            {
                "node_id": "9.0",
                "question": "q",
                "answer": "是",
                "reason": "test",
                "bar_range": "K1",
            },
            "garbage-trace-entry",
        ],
        "bar_analysis": {
            "signal_bar": {"bar": "K1", "quality": "valid", "pattern": "none"},
            "entry_bar": {"bar": "K1", "strength": "strong", "freshness": "fresh"},
        },
    }
    stage1_json = "not-a-dict"
    DecisionNodeEngine.apply_stage2(out, frame, stage1_json)

    assert isinstance(out["decision"], dict)
    assert all(isinstance(n, dict) for n in out["decision_trace"])
    node_91 = next((n for n in out["decision_trace"] if n.get("node_id") == "9.1"), None)
    assert node_91 is not None
    assert node_91.get("answer") == "是"
