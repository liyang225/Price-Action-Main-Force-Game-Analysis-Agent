"""Integration test: cancel_token set before stage 2 starts.

Task 11.10
"""
from __future__ import annotations

from unittest.mock import MagicMock

from tests.fixtures.validators import schema_test_validator
from pa_agent.ai.router import route_strategy_files
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent

from .conftest import VALID_STAGE1, make_reply


def test_cancel_before_stage2(frame, pending_writer, assembler, exp_reader):
    """cancel_token set after stage1 succeeds → Cancelled event, no Stage2, count unchanged."""
    cancel_token = CancelToken()

    # After stage1 chat returns, set the cancel token so the pre-stage2 check fires
    stage1_reply = make_reply(VALID_STAGE1)

    def chat_side_effect(messages, **kwargs):
        # Set cancel after the first (stage1) call returns
        cancel_token.set()
        return stage1_reply

    client = MagicMock()
    client.stream_chat.side_effect = chat_side_effect

    validator = schema_test_validator()
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=validator,
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    events: list[OrchestratorEvent] = []

    orchestrator.submit(
        frame=frame,
        cancel_token=cancel_token,
        on_event=events.append,
    )

    # Cancelled event must appear
    assert OrchestratorEvent.Cancelled in events

    # Stage2Started must NOT appear
    assert OrchestratorEvent.Stage2Started not in events

    # save_partial called with reason "user_cancelled"
    pending_writer.save_partial.assert_called_once()
    call_args = pending_writer.save_partial.call_args
    reason = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("reason", "")
    assert reason == "user_cancelled"
