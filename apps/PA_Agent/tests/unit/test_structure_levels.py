"""Tests for deterministic Stage 1 support/resistance refresh."""
from __future__ import annotations

from pa_agent.ai.stage1_normalizer import normalize_stage1
from pa_agent.ai.structure_levels import refresh_stage1_support_resistance
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame


def _frame_from_closes(closes: list[float]) -> KlineFrame:
    bars: list[KlineBar] = []
    for i, close in enumerate(closes):
        bars.append(
            KlineBar(
                seq=i + 1,
                ts_open=float(1000 - i),
                open=close,
                high=close + 5,
                low=close - 5,
                close=close,
                volume=1.0,
                closed=True,
            )
        )
    return KlineFrame(
        symbol="BTCUSDm",
        timeframe="5m",
        bars=tuple(bars),
        indicators=IndicatorBundle(ema20=tuple([float("nan")] * len(bars)), atr14=tuple([10.0] * len(bars))),
        snapshot_ts_local_ms=0,
    )


def test_refresh_drops_broken_resistance_and_refills() -> None:
    frame = _frame_from_closes([64360.0, 64340.0, 64320.0, 64300.0, 64280.0])
    stage1 = {
        "support_levels": ["64307", "64282"],
        "resistance_levels": ["64353", "64373"],
    }
    assert refresh_stage1_support_resistance(stage1, frame) is True
    assert all(float(x) < 64360.0 for x in stage1["support_levels"])
    assert all(float(x) > 64360.0 for x in stage1["resistance_levels"])
    assert "64353" not in stage1["resistance_levels"]


def test_normalize_stage1_applies_support_resistance_refresh() -> None:
    frame = _frame_from_closes([64360.0, 64340.0, 64320.0, 64300.0, 64280.0])
    obj = {
        "cycle_position": "trading_range",
        "direction": "neutral",
        "diagnosis_confidence": 55,
        "market_phase": "stable",
        "key_signals": [],
        "htf_context": "",
        "entry_setup": "",
        "support_levels": ["64307"],
        "resistance_levels": ["64353"],
        "strategy_files_needed": [],
        "risk_warning": "",
        "bar_analysis": {
            "always_in": "neutral",
            "last_closed_bar": "K1",
            "bar_type": "doji",
            "signal_bar": {"bar": None, "quality": "invalid", "reason": "t"},
            "entry_setup_type": "none",
            "follow_through": "pending",
        },
        "bar_by_bar_summary": [],
        "gate_trace": [
            {
                "node_id": "1.2",
                "question": "q",
                "answer": "是",
                "reason": "r",
                "branch": "trading_range",
                "section": "s",
                "bar_range": "K5-K1",
            }
        ],
        "gate_result": "proceed",
    }
    out = normalize_stage1(obj, kline_frame=frame)
    assert "64353" not in (out.get("resistance_levels") or [])
    assert out["resistance_levels"]
    assert out["support_levels"]


def test_merge_sorts_support_near_to_far() -> None:
    """Swing refill must not append nearest pivots at the tail (chart uses farthest)."""
    from pa_agent.ai.structure_levels import _merge_level_texts

    merged = _merge_level_texts(
        ["4139", "4121"],
        [4174.938],
        tick=0.001,
        max_levels=3,
        kind="support",
    )
    assert merged[0] == "4174.938"
    assert merged[-1] == "4121"
    assert "4139" in merged


def test_merge_keeps_farthest_ai_when_swings_cluster() -> None:
    from pa_agent.ai.structure_levels import _merge_level_texts

    merged = _merge_level_texts(
        ["4139", "4121"],
        [4147.8, 4147.4, 4147.1],
        tick=0.001,
        max_levels=3,
        kind="support",
    )
    assert merged[0] == "4147.8"
    assert merged[-1] == "4121"
    assert "4139" in merged


def test_merge_sorts_resistance_near_to_far() -> None:
    from pa_agent.ai.structure_levels import _merge_level_texts

    merged = _merge_level_texts(
        ["4200", "4221"],
        [4178.69],
        tick=0.001,
        max_levels=3,
        kind="resistance",
    )
    assert merged == ["4178.69", "4200", "4221"]
