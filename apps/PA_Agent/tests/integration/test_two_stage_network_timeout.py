"""Integration test: stage 1 raises a network timeout error.

Task 11.9
"""
from __future__ import annotations

from unittest.mock import MagicMock

import openai

from tests.fixtures.validators import schema_test_validator
from pa_agent.ai.router import route_strategy_files
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent


def test_httpx_read_error_stage1(frame, pending_writer, assembler, exp_reader):
    """httpx.ReadError (e.g. WinError 10054 mid-stream) → Stage1Failed + partial save."""
    try:
        import httpx
    except ImportError:
        return

    client = MagicMock()
    client.stream_chat.side_effect = httpx.ReadError(
        "[WinError 10054] 远程主机强迫关闭了一个现有的连接。"
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
    orchestrator.submit(
        frame=frame,
        cancel_token=CancelToken(),
        on_event=events.append,
    )

    assert OrchestratorEvent.Stage1Failed in events
    pending_writer.save_partial.assert_called_once()
    assert pending_writer.save_partial.call_args[0][1] == "network_error"


def test_network_timeout_stage1(frame, pending_writer, assembler, exp_reader):
    """APITimeoutError on stage1 → Stage1Failed emitted."""
    client = MagicMock()
    # openai.APITimeoutError requires a `request` parameter
    client.stream_chat.side_effect = openai.APITimeoutError(request=MagicMock())

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

    # Stage1Failed event must appear
    assert OrchestratorEvent.Stage1Failed in events

    # Stage2 must never start
    assert OrchestratorEvent.Stage2Started not in events

    # save_partial called with reason "network_error"
    pending_writer.save_partial.assert_called_once()
    call_args = pending_writer.save_partial.call_args
    reason = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("reason", "")
    assert reason == "network_error"
