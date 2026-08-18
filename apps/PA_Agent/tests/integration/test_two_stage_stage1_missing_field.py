"""Integration test: stage 1 JSON is missing a required field.

Task 11.6
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from tests.fixtures.validators import schema_test_validator
from pa_agent.ai.router import route_strategy_files
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent

from .conftest import VALID_STAGE1


def _make_reply(content_dict: dict) -> MagicMock:
    reply = MagicMock()
    reply.content = json.dumps(content_dict)
    reply.raw = {"content": reply.content}
    reply.usage = MagicMock()
    reply.usage.prompt_tokens = 100
    reply.usage.completion_tokens = 50
    reply.usage.cached_prompt_tokens = 0
    reply.usage.total_tokens = 150
    return reply


def test_stage1_missing_cycle_position(frame, pending_writer, assembler, exp_reader):
    """Stage 1 JSON missing cycle_position → category 'b'."""
    # Build stage1 JSON without cycle_position
    bad_stage1 = {k: v for k, v in VALID_STAGE1.items() if k != "cycle_position"}

    client = MagicMock()
    client.stream_chat.return_value = _make_reply(bad_stage1)

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

    # Validation error category should be 'b' (missing field)
    assert record.exception is not None
    assert record.exception["category"] == "b"

    # Stage2 never started
    assert OrchestratorEvent.Stage2Started not in events
