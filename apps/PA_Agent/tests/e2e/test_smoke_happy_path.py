"""E2E smoke test — happy path: two-stage analysis produces a trading decision.

Task 19.1
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from pa_agent.app_context import AppContext
from tests.fixtures.kline_bars import make_newest_first_bars
from tests.fixtures.validators import schema_test_validator
from pa_agent.ai.router import route_strategy_files

from tests.fixtures.ai_payloads import VALID_STAGE1, VALID_STAGE2_ORDER


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


def _make_ctx(tmp_path, stage2_response=None):
    """Build a minimal AppContext with all required components."""
    if stage2_response is None:
        stage2_response = VALID_STAGE2_ORDER

    mock_client = MagicMock()
    mock_client.stream_chat.side_effect = [
        _make_reply(VALID_STAGE1),
        _make_reply(stage2_response),
    ]

    mock_assembler = MagicMock()
    mock_assembler.build_stage1.return_value = [{"role": "system", "content": "s1"}]
    mock_assembler.build_stage2.return_value = [{"role": "system", "content": "s2"}]

    pending_writer = MagicMock()

    ctx = AppContext()
    ctx.client = mock_client
    ctx.assembler = mock_assembler
    ctx.router = route_strategy_files
    ctx.validator = schema_test_validator()
    ctx.pending_writer = pending_writer
    ctx.exp_reader = MagicMock()
    ctx.exp_reader.read_top5.return_value = []

    return ctx, pending_writer


@pytest.mark.e2e
def test_happy_path_shows_trading_decision(qtbot, tmp_path):
    """Full two-stage analysis completes and DecisionPanel shows a trade."""
    from pa_agent.gui.main_window import MainWindow

    ctx, pending_writer = _make_ctx(tmp_path)

    window = MainWindow(ctx)
    qtbot.addWidget(window)
    window.show()

    window._ctx.settings.general.analysis_bar_count = 5
    window._last_frame_ready_bars = make_newest_first_bars(9, with_forming=True)

    window._on_submit_analysis()

    # Poll until the analysis is no longer in progress (worker done).
    # Using waitUntil avoids the race where the worker finishes before
    # waitSignal is set up (mock data is very fast).
    qtbot.waitUntil(
        lambda: not window._analysis_in_progress,
        timeout=10_000,
    )

    # DecisionPanel should show a trading decision (not 不下单)
    conclusion_text = window._decision_panel._conclusion_label.text()
    assert "不下单" not in conclusion_text, (
        f"Expected a trading decision, got: {conclusion_text!r}"
    )
    assert conclusion_text != "—", (
        "DecisionPanel still shows default '—', expected a decision"
    )

    # PendingWriter.save_full should have been called
    pending_writer.save_full.assert_called_once()
