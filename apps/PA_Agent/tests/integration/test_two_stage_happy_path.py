"""Integration test: happy path — both stages succeed.

Task 11.4
"""
from __future__ import annotations

from unittest.mock import MagicMock

from tests.fixtures.validators import schema_test_validator
from pa_agent.ai.router import route_strategy_files
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent

from .conftest import VALID_STAGE1, VALID_STAGE2, make_reply


def test_happy_path(frame, pending_writer, assembler, exp_reader):
    """Both stages return valid JSON → full record saved, counter stays at 0."""
    client = MagicMock()
    client.stream_chat.side_effect = [
        make_reply(VALID_STAGE1),
        make_reply(VALID_STAGE2),
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

    events: list[OrchestratorEvent] = []
    cancel_token = CancelToken()

    record = orchestrator.submit(
        frame=frame,
        cancel_token=cancel_token,
        on_event=events.append,
    )

    # Event sequence
    assert events == [
        OrchestratorEvent.Stage1Started,
        OrchestratorEvent.Stage1Done,
        OrchestratorEvent.Stage2Started,
        OrchestratorEvent.Stage2Done,
        OrchestratorEvent.RecordSaved,
    ]

    # Record has both stages populated
    assert record.stage1_diagnosis is not None
    assert record.stage2_decision is not None

    # save_full was called (not save_partial)
    pending_writer.save_full.assert_called_once_with(record)
    pending_writer.save_partial.assert_not_called()
