"""Integration tests for next_bar_prediction feature (T19).

Covers: orchestrator pass-through, zero extra AI calls, short-circuit,
logging, round-trip persistence, demo mode legacy, cancel, network error.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import pytest

from tests.fixtures.validators import schema_test_validator
from pa_agent.ai.router import route_strategy_files
from pa_agent.data.base import KlineFrame
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent

from .conftest import (
    VALID_STAGE1,
    VALID_STAGE2,
    make_reply,
    make_frame,
)

_PREDICTION_BULLISH = {
    "direction": "bullish",
    "probabilities": {"bullish": 60, "bearish": 30, "neutral": 10},
    "reasoning": "???????????????????????",
    "unpredictable": False,
    "features_used": ["stage1_diagnosis"],
}

_PREDICTION_UNPREDICTABLE = {
    "direction": None,
    "probabilities": None,
    "reasoning": "K??????????????????",
    "unpredictable": True,
    "features_used": ["stage1_diagnosis"],
}


def _make_stage2_with_prediction(prediction: dict) -> dict:
    """Return a valid Stage 2 JSON with the given prediction."""
    s2 = json.loads(json.dumps(VALID_STAGE2))  # deep copy
    s2["next_bar_prediction"] = prediction
    return s2


@pytest.fixture
def frame():
    return make_frame()


@pytest.fixture
def pending_writer():
    return MagicMock()


@pytest.fixture
def assembler():
    mock = MagicMock()
    mock.build_stage1.return_value = [{"role": "system", "content": "test stage1"}]
    mock.build_stage2.return_value = [{"role": "system", "content": "test stage2"}]
    mock.build_stage2_continuation.return_value = [
        {"role": "system", "content": "test stage2"},
        {"role": "user", "content": "test"},
    ]
    return mock


@pytest.fixture
def exp_reader():
    mock = MagicMock()
    mock.read_top5.return_value = []
    return mock


# ?? Tests ????????????????????????????????????????????????????????????????????


def test_orchestrator_passes_through_prediction(
    frame, pending_writer, assembler, exp_reader
):
    """AI returns prediction ? record.stage2_decision contains it (R1.1)."""
    client = MagicMock()
    stage2_with_pred = _make_stage2_with_prediction(_PREDICTION_BULLISH)
    client.stream_chat.side_effect = [
        make_reply(VALID_STAGE1),
        make_reply(stage2_with_pred),
    ]

    validator = schema_test_validator()
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=validator,
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    record = orchestrator.submit(
        frame=frame,
        cancel_token=CancelToken(),
        on_event=lambda e: None,
    )

    pred = record.stage2_decision.get("next_bar_prediction")
    assert isinstance(pred, dict), f"Expected dict, got {type(pred)}"
    assert pred["direction"] == "bullish"
    assert pred["probabilities"]["bullish"] == 60


def test_orchestrator_calls_client_twice_max(
    frame, pending_writer, assembler, exp_reader
):
    """Prediction must NOT cause extra AI calls (R4.3, NFR1.1)."""
    client = MagicMock()
    client.stream_chat.side_effect = [
        make_reply(VALID_STAGE1),
        make_reply(_make_stage2_with_prediction(_PREDICTION_BULLISH)),
    ]

    validator = schema_test_validator()
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=validator,
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    orchestrator.submit(frame=frame, cancel_token=CancelToken(), on_event=lambda e: None)
    assert client.stream_chat.call_count == 2


def test_short_circuit_emits_unpredictable(
    frame, pending_writer, assembler, exp_reader
):
    """gate_result=wait ? short-circuit response with unpredictable prediction (R4.6)."""
    wait_stage1 = json.loads(json.dumps(VALID_STAGE1))
    wait_stage1["gate_result"] = "wait"
    wait_stage1["cycle_position"] = "unknown"
    wait_stage1["gate_trace"] = [
        {
            "node_id": "1.2",
            "question": "\u662f\u5426\u80fd\u8bc6\u522b\u5e02\u573a\u5468\u671f\uff1f",
            "answer": "\u5426",
            "action": "\u7b49\u5f85",
            "reason": "\u65e0\u6cd5\u8bc6\u522b\u5468\u671f",
            "bar_range": "K5-K1",
            "branch": "unknown",
        },
    ]

    client = MagicMock()
    client.stream_chat.return_value = make_reply(wait_stage1)

    validator = schema_test_validator()
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=validator,
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    record = orchestrator.submit(frame=frame, cancel_token=CancelToken(), on_event=lambda e: None)
    pred = record.stage2_decision.get("next_bar_prediction")
    assert isinstance(pred, dict)
    assert pred["unpredictable"] is True


def test_log_emits_prediction_line(
    frame, pending_writer, assembler, exp_reader, caplog
):
    """Stage 2 completion must log prediction info (R9.3)."""
    client = MagicMock()
    client.stream_chat.side_effect = [
        make_reply(VALID_STAGE1),
        make_reply(_make_stage2_with_prediction(_PREDICTION_BULLISH)),
    ]

    validator = schema_test_validator()
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=validator,
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    with caplog.at_level(logging.INFO, logger="pa_agent.orchestrator.two_stage"):
        orchestrator.submit(frame=frame, cancel_token=CancelToken(), on_event=lambda e: None)

    assert any("next_bar_prediction" in r.message for r in caplog.records), (
        f"Expected prediction log line, got: {[r.message for r in caplog.records]}"
    )


def test_save_full_round_trip(
    frame, pending_writer, assembler, exp_reader
):
    """Write ? reload ? prediction fields preserved (R9.4)."""
    client = MagicMock()
    client.stream_chat.side_effect = [
        make_reply(VALID_STAGE1),
        make_reply(_make_stage2_with_prediction(_PREDICTION_BULLISH)),
    ]

    validator = schema_test_validator()
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=validator,
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    record = orchestrator.submit(frame=frame, cancel_token=CancelToken(), on_event=lambda e: None)

    # Verify saved record has the prediction
    saved = pending_writer.save_full.call_args[0][0]
    pred = saved.stage2_decision.get("next_bar_prediction")
    assert pred is not None
    assert pred["direction"] == "bullish"


def test_demo_mode_replays_legacy_record(
    frame, pending_writer, assembler, exp_reader
):
    """Legacy record without prediction must not crash DecisionPanel (R10.1)."""
    from pa_agent.gui.decision_panel import DecisionPanel
    from PyQt6.QtWidgets import QApplication
    import sys

    app = QApplication.instance() or QApplication(sys.argv)
    panel = DecisionPanel()

    # Legacy: no next_bar_prediction key
    legacy_s2 = json.loads(json.dumps(VALID_STAGE2))
    # Ensure no prediction key
    legacy_s2.pop("next_bar_prediction", None)

    # Must not raise
    panel.set_decision(legacy_s2)
    assert not panel._prediction_group.isVisible()


def test_cancel_no_prediction_required(
    frame, pending_writer, assembler, exp_reader
):
    """Early cancel must not require prediction (R7.5)."""
    cancel_token = CancelToken()
    cancel_token.set()  # Pre-cancel

    client = MagicMock()
    validator = schema_test_validator()
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=validator,
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    # Should not raise, and client should not be called
    record = orchestrator.submit(frame=frame, cancel_token=cancel_token, on_event=lambda e: None)
    assert client.stream_chat.call_count == 0


def test_network_error_no_prediction_required(
    frame, pending_writer, assembler, exp_reader
):
    """Network error ? next_bar_prediction missing is not an extra failure (R7.5)."""
    try:
        import openai
        err = openai.APIConnectionError(request=MagicMock())
    except ImportError:
        err = ConnectionError("timeout")

    client = MagicMock()
    client.stream_chat.side_effect = err

    validator = schema_test_validator()
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=validator,
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    record = orchestrator.submit(frame=frame, cancel_token=CancelToken(), on_event=lambda e: None)
    # Record should have an exception, but no prediction
    assert record.exception is not None
    pred = record.stage2_decision.get("next_bar_prediction") if isinstance(record.stage2_decision, dict) else None
    # prediction may be None ? that's fine
    assert pred is None or isinstance(pred, dict)
