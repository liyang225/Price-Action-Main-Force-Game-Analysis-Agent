"""Integration test: stage 1 returns plain text (not JSON).

Task 11.5
"""
from __future__ import annotations

from unittest.mock import MagicMock

from tests.fixtures.validators import schema_test_validator
from pa_agent.ai.router import route_strategy_files
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent


def _make_text_reply(text: str) -> MagicMock:
    reply = MagicMock()
    reply.content = text
    reply.raw = {"content": text}
    reply.usage = MagicMock()
    reply.usage.prompt_tokens = 100
    reply.usage.completion_tokens = 50
    reply.usage.cached_prompt_tokens = 0
    reply.usage.total_tokens = 150
    return reply


def test_stage1_plain_text(frame, pending_writer, assembler, exp_reader):
    """Stage 1 returns plain text → Stage2 never starts."""
    client = MagicMock()
    client.stream_chat.return_value = _make_text_reply(
        "Sorry, I cannot provide a JSON response right now."
    )

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

    orchestrator.submit(
        frame=frame,
        cancel_token=cancel_token,
        on_event=events.append,
    )

    # save_partial was called with a reason containing "stage1"
    pending_writer.save_partial.assert_called_once()
    call_args = pending_writer.save_partial.call_args
    reason = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("reason", "")
    assert "stage1" in reason

    # Stage2Started must NOT appear
    assert OrchestratorEvent.Stage2Started not in events
