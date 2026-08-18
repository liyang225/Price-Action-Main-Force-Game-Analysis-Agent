"""Integration: Stage1 gate wait short-circuits Stage2 API call."""
from __future__ import annotations

import copy
from unittest.mock import MagicMock

from tests.fixtures.validators import schema_test_validator
from pa_agent.ai.router import route_strategy_files
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent

from .conftest import VALID_STAGE1, make_reply


def test_gate_wait_skips_stage2_chat(
    frame, pending_writer, assembler, exp_reader,
) -> None:
    stage1_wait = copy.deepcopy(VALID_STAGE1)
    stage1_wait["gate_result"] = "wait"
    stage1_wait["gate_trace"] = [
        {
            "node_id": "1.2",
            "question": "是否能识别市场周期？",
            "answer": "否",
            "action": "等待",
            "reason": "无法识别周期",
            "bar_range": "K5-K1",
        }
    ]
    stage1_wait["cycle_position"] = "unknown"

    client = MagicMock()
    client.stream_chat.return_value = make_reply(stage1_wait)

    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=schema_test_validator(),
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    events: list[OrchestratorEvent] = []
    record = orchestrator.submit(
        frame=frame,
        cancel_token=CancelToken(),
        on_event=events.append,
    )

    assert client.stream_chat.call_count == 1
    assert record.stage2_decision is not None
    assert record.stage2_decision.get("gate_shortcircuited") is True
    assert record.stage2_decision["decision"]["order_type"] == "不下单"
    assert OrchestratorEvent.Stage2Done in events
