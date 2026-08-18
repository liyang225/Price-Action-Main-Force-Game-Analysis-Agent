# -*- coding: utf-8 -*-
"""Shared test infrastructure for TwoStageOrchestrator integration tests."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from tests.fixtures.gate_trace import make_bar_by_bar_summary, make_mandatory_gate_trace_proceed

SAMPLE_GATE_TRACE = make_mandatory_gate_trace_proceed(max_seq=20)

SAMPLE_DECISION_TRACE = [
    {
        "node_id": "4.1",
        "section": "\u987a\u52bf\u901a\u9053",
        "question": "\u662f\u5426\u4e3a\u987a\u52bf\u901a\u9053\u7ed3\u6784\uff1f",
        "answer": "\u662f",
        "reason": "HH+HL \u7ed3\u6784\u6e05\u6670\uff0c\u56de\u8c03\u6d45\u4e14\u8ddf\u968f\u826f\u597d\u3002",
        "skipped": False,
        "bar_range": "K5-K3",
    },
    {
        "node_id": "9.1",
        "section": "\u4fe1\u53f7\u68d2",
        "question": "\u6700\u65b0K\u7ebf\u662f\u5426\u4e3a\u5408\u683c\u4fe1\u53f7\u68d2\uff1f",
        "answer": "\u662f",
        "reason": "\u4fe1\u53f7\u68d2\u5b9e\u4f53\u9971\u6ee1\uff0c\u65b9\u5411\u4e0e\u591a\u5934\u5224\u65ad\u4e00\u81f4\u3002",
        "skipped": False,
        "bar_range": "K2",
    },
    {
        "node_id": "9.2",
        "section": "\u4fe1\u53f7\u68d2",
        "question": "\u4fe1\u53f7\u68d2\u662f\u5426\u5f97\u5230\u524d\u5e8fK\u7ebf\u786e\u8ba4\uff1f",
        "answer": "\u662f",
        "reason": "\u524d\u4e00\u6839K\u7ebf\u63d0\u4f9b\u65b9\u5411\u94fa\u57ab\uff0c\u4fe1\u53f7\u68d2\u7a81\u7834\u524d\u9ad8\u3002",
        "skipped": False,
        "bar_range": "K1",
    },
    {
        "node_id": "10.1",
        "section": "\u5165\u573a\u68d2",
        "question": "\u5165\u573a\u68d2\u662f\u5426\u5f3a\u52b2\uff1f",
        "answer": "\u662f",
        "reason": "\u5165\u573a\u68d2\u8ddf\u968f\u4fe1\u53f7\u68d2\uff0c\u6536\u76d8\u9760\u8fd1\u9ad8\u70b9\u3002",
        "skipped": False,
        "bar_range": "K1",
    },
    {
        "node_id": "10.2",
        "section": "\u5165\u573a\u68d2",
        "question": "\u662f\u5426\u6ee1\u8db3\u4e8c\u6b21\u5165\u573a\u6761\u4ef6\uff1f",
        "answer": "\u662f",
        "reason": "\u7a81\u7834\u6d4b\u8bd5\u540e\u56de\u8e29\u4e0d\u7834\u5173\u952e\u4f4e\u70b9\u3002",
        "skipped": False,
        "bar_range": "K4-K2",
    },
    {
        "node_id": "10.3",
        "section": "\u4ea4\u6613\u8005\u65b9\u7a0b",
        "question": "\u4ea4\u6613\u8005\u65b9\u7a0b\u662f\u5426\u901a\u8fc7\uff1f",
        "answer": "\u662f",
        "reason": "entry=2000 stop=1980 target=2050, RR about 2.5:1.",
        "skipped": False,
        "bar_range": "K1",
    },
    {
        "node_id": "11.2",
        "section": "\u6700\u7ec8\u786e\u8ba4",
        "question": "\u662f\u5426\u6267\u884c\u8be5\u4ea4\u6613\u8ba1\u5212\uff1f",
        "answer": "\u662f",
        "reason": "\u95f8\u95e8\u4e0e\u9636\u6bb5\u4e8c\u51b3\u7b56\u6811\u8282\u70b9\u5747\u5df2\u901a\u8fc7\u3002",
        "skipped": False,
        "bar_range": "K3-K1",
    },
]

SAMPLE_BAR_BY_BAR_SUMMARY = make_bar_by_bar_summary(5)

VALID_STAGE1 = {
    "cycle_position": "normal_channel",
    "direction": "bullish",
    "diagnosis_confidence": 75,
    "market_phase": "stable",
    "detected_patterns": [],
    "key_signals": ["signal1"],
    "htf_context": "bullish trend",
    "entry_setup": "buy on pullback",
    "strategy_files_needed": ["\u4e0a\u6da8\u901a\u9053\u5206\u6790\u8bc6\u522b.txt"],
    "bar_by_bar_summary": SAMPLE_BAR_BY_BAR_SUMMARY,
    "gate_trace": SAMPLE_GATE_TRACE,
    "gate_result": "proceed",
}

SAMPLE_BAR_ANALYSIS = {
    "always_in": "long",
    "last_closed_bar": "K1",
    "bar_type": "trend_bull",
    "signal_bar": {
        "bar": "K2",
        "quality": "strong",
        "pattern": "H1",
        "reason": "test",
    },
    "entry_bar": {
        "bar": "K1",
        "strength": "strong",
        "follow_through": True,
        "still_valid": True,
        "freshness": "fresh",
    },
    "second_entry": {"is_second_entry": False, "type": "none"},
}

VALID_STAGE2 = {
    "decision": {
        "order_direction": "\u505a\u591a",
        "order_type": "\u7a81\u7834\u5355",
        "entry_price": 2010.5,
        "take_profit_price": 2050.0,
        "take_profit_price_2": 2090.0,
        "stop_loss_price": 1995.0,
        "entry_basis_bar": "K2",
        "entry_basis_extreme": "high",
        "entry_rule": "long breakout above K2 high by 1 tick",
        "reasoning": "Strong bullish signal",
        "diagnosis_confidence": 75,
        "diagnosis_confidence_reasoning": "stage1 diagnosis consistent",
        "trade_confidence": 70,
        "trade_confidence_reasoning": "signal and entry bars strong",
        "estimated_win_rate": 55,
        "estimated_win_rate_reasoning": "RR and structure support ~55%",
        "key_factors": ["factor1"],
        "watch_points": ["watch1"],
        "risk_assessment": "low risk",
        "invalidation_condition": "break below 1980",
    },
    "diagnosis_summary": {
        "cycle_position": "normal_channel",
        "direction": "bullish",
        "key_signals": ["signal1"],
    },
    "bar_analysis": SAMPLE_BAR_ANALYSIS,
    "decision_trace": SAMPLE_DECISION_TRACE,
    "terminal": {
        "node_id": "11.2",
        "outcome": "trade",
        "label": "10.3 equation pass, breakout long",
    },
}


def make_reply(content_dict: dict) -> MagicMock:
    """Build a mock AIReply from a content dict."""
    reply = MagicMock()
    reply.content = json.dumps(content_dict, ensure_ascii=False)
    reply.reasoning_content = ""
    reply.raw = {"content": reply.content}
    reply.latency_ms = 1.0
    reply.usage = MagicMock()
    reply.usage.prompt_tokens = 100
    reply.usage.completion_tokens = 50
    reply.usage.cached_prompt_tokens = 0
    reply.usage.total_tokens = 150
    return reply


def make_frame() -> KlineFrame:
    """Build a minimal KlineFrame for testing (20 bars, bullish trend to pass PreflightDataGate)."""
    n = 20
    # Construct bars with a clear bullish trend: price rises from K20 (oldest) to K1 (newest)
    # seq=1 is newest (bars[0]), seq=20 is oldest (bars[19])
    bars = tuple(
        KlineBar(
            seq=i + 1,
            ts_open=1000 - i * 60000,
            open=2000.0 + (n - 1 - i) * 2.0,   # older bars have lower price
            high=2010.0 + (n - 1 - i) * 2.0,
            low=1990.0 + (n - 1 - i) * 2.0,
            close=2005.0 + (n - 1 - i) * 2.0,   # close rises: K20=close~2005, K1=close~2043
            volume=100.0,
            closed=(i > 0),
        )
        for i in range(n)
    )
    # EMA rising: newer bars (index 0=K1) have higher EMA than older bars
    ema_values = tuple(2000.0 + (n - 1 - i) * 1.5 for i in range(n))
    atr_values = tuple([10.0] * n)
    indicators = IndicatorBundle(
        ema20=ema_values,
        atr14=atr_values,
    )
    return KlineFrame(
        symbol="XAUUSD",
        timeframe="1h",
        bars=bars,
        snapshot_ts_local_ms=1700000000000,
        indicators=indicators,
    )


@pytest.fixture
def frame():
    return make_frame()


@pytest.fixture
def pending_writer():
    return MagicMock()


@pytest.fixture
def assembler():
    mock = MagicMock()
    mock.build_stage1.return_value = [{"role": "system", "content": "test"}]
    mock.build_stage2.return_value = [{"role": "system", "content": "test"}]
    mock.build_stage2_continuation.return_value = [
        {"role": "system", "content": "test"},
        {"role": "user", "content": "test"},
    ]
    return mock


@pytest.fixture
def exp_reader():
    mock = MagicMock()
    mock.read_top5.return_value = []
    mock.read_for_stage2.return_value = []
    return mock
