"""Tests for §11 order-method routing."""
from __future__ import annotations

from pa_agent.ai.decision_nodes import route_order_method


def test_breakout_without_basis_falls_back_to_limit() -> None:
    decision = {
        "order_type": "突破单",
        "entry_price": 101.0,
        "stop_loss_price": 99.0,
        "take_profit_price": 102.0,
    }
    trace = [{"node_id": "10.3", "answer": "是", "reason": "ok"}]
    stage1 = {"cycle_position": "normal_channel"}
    nodes = route_order_method(stage1, decision, trace)
    assert decision["order_type"] == "限价单"
    assert nodes
    assert nodes[-1].node_id == "11.2"
    assert nodes[-1].answer == "是"
    assert "限价单" in nodes[-1].reason


def test_model_breakout_preserved_for_broad_channel() -> None:
    decision = {
        "order_type": "突破单",
        "order_direction": "做空",
        "entry_price": 4210.348,
        "entry_basis_bar": "K1",
        "entry_basis_extreme": "low",
        "stop_loss_price": 4228.399,
        "take_profit_price": 4183.278,
    }
    trace = [{"node_id": "10.3", "answer": "是", "reason": "ok"}]
    stage1 = {"cycle_position": "broad_channel"}
    nodes = route_order_method(stage1, decision, trace)
    assert decision["order_type"] == "突破单"
    assert nodes
    assert nodes[-1].node_id == "11.2"
    assert nodes[-1].answer == "是"


def test_model_limit_order_preserved_for_breakout_cycle() -> None:
    decision = {
        "order_type": "限价单",
        "entry_price": 100.5,
        "stop_loss_price": 99.0,
        "take_profit_price": 101.5,
    }
    trace = [{"node_id": "10.3", "answer": "是", "reason": "ok"}]
    stage1 = {"cycle_position": "normal_channel"}
    nodes = route_order_method(stage1, decision, trace)
    assert decision["order_type"] == "限价单"
    assert nodes[-1].answer == "是"
