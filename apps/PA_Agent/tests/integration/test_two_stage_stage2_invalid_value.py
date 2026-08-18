"""Integration test: stage 2 JSON has an invalid enum value.

Task 11.7
"""
from __future__ import annotations

import copy
import json
from unittest.mock import MagicMock

from tests.fixtures.validators import schema_test_validator
from pa_agent.ai.router import route_strategy_files
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent

from .conftest import VALID_STAGE1, VALID_STAGE2, make_reply


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


def test_stage2_invalid_confidence(frame, pending_writer, assembler, exp_reader):
    """Stage 2 has trade_confidence='ultra' (invalid type) → category 'c'."""
    bad_stage2 = copy.deepcopy(VALID_STAGE2)
    bad_stage2["decision"]["trade_confidence"] = "ultra"

    client = MagicMock()
    client.stream_chat.side_effect = [
        make_reply(VALID_STAGE1),
        _make_reply(bad_stage2),
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

    # Validation error category should be 'c' (invalid value)
    assert record.exception is not None
    assert record.exception["category"] == "c"

    # Stage1 succeeded, Stage2 failed
    assert OrchestratorEvent.Stage1Done in events
    assert OrchestratorEvent.Stage2Started in events
    assert OrchestratorEvent.Stage2Failed in events
    assert OrchestratorEvent.RecordSaved not in events
