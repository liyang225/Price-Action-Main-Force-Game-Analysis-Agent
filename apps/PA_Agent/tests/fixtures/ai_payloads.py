"""Shared valid Stage1/Stage2 JSON payloads for unit, integration, and e2e tests."""
from __future__ import annotations

from tests.integration.conftest import (
    SAMPLE_DECISION_TRACE,
    SAMPLE_GATE_TRACE,
    VALID_STAGE1,
    VALID_STAGE2,
)

__all__ = [
    "SAMPLE_DECISION_TRACE",
    "SAMPLE_GATE_TRACE",
    "VALID_STAGE1",
    "VALID_STAGE2",
    "VALID_STAGE2_NO_ORDER",
    "VALID_STAGE2_ORDER",
]

VALID_STAGE2_ORDER = VALID_STAGE2

VALID_STAGE2_NO_ORDER = {
    "decision": {
        "order_direction": None,
        "order_type": "不下单",
        "entry_price": None,
        "take_profit_price": None,
        "take_profit_price_2": None,
        "stop_loss_price": None,
        "reasoning": "No clear setup",
        "diagnosis_confidence": 60,
        "diagnosis_confidence_reasoning": "周期尚可",
        "trade_confidence": 35,
        "trade_confidence_reasoning": "无合格入场",
        "estimated_win_rate": None,
        "estimated_win_rate_reasoning": "未下单，无胜率估计",
        "key_factors": [],
        "watch_points": ["等待信号"],
        "risk_assessment": "观望",
        "invalidation_condition": None,
    },
    "diagnosis_summary": {
        "cycle_position": "trading_range",
        "direction": "neutral",
        "key_signals": [],
    },
    "decision_trace": [
        {
            "node_id": "6.3",
            "section": "交易区间",
            "question": "当前价格是否在区间边界？",
            "answer": "否",
            "reason": "价格在区间中部",
            "skipped": False,
            "bar_range": "K40-K1",
        },
    ],
    "terminal": {
        "node_id": "6.3",
        "outcome": "wait",
        "label": "区间中部不交易，未进入 §9–§10",
    },
}
