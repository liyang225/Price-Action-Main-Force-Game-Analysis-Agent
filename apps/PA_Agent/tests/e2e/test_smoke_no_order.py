"""E2E smoke test — no-order path: stage2 returns 不下单.

Task 19.2
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import pyqtgraph as pg

from pa_agent.app_context import AppContext
from tests.fixtures.kline_bars import make_newest_first_bars
from tests.fixtures.validators import schema_test_validator
from pa_agent.ai.router import route_strategy_files

from tests.fixtures.ai_payloads import VALID_STAGE1, VALID_STAGE2_NO_ORDER


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


def _make_ctx(tmp_path):
    """Build a minimal AppContext configured for a no-order response."""
    mock_client = MagicMock()
    mock_client.stream_chat.side_effect = [
        _make_reply(VALID_STAGE1),
        _make_reply(VALID_STAGE2_NO_ORDER),
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
def test_no_order_shows_no_trade_conclusion(qtbot, tmp_path):
    """When stage2 returns 不下单, DecisionPanel shows that conclusion."""
    from pa_agent.gui.main_window import MainWindow

    ctx, pending_writer = _make_ctx(tmp_path)

    window = MainWindow(ctx)
    qtbot.addWidget(window)
    window.show()

    window._ctx.settings.general.analysis_bar_count = 5
    window._last_frame_ready_bars = make_newest_first_bars(9, with_forming=True)
    window._on_submit_analysis()

    # Poll until the analysis is no longer in progress
    qtbot.waitUntil(
        lambda: not window._analysis_in_progress,
        timeout=10_000,
    )

    # DecisionPanel should show 不下单
    conclusion_text = window._decision_panel._conclusion_label.text()
    assert "不下单" in conclusion_text, (
        f"Expected 不下单 conclusion, got: {conclusion_text!r}"
    )

    # Chart should have no InfiniteLine items (no entry/TP/SL lines)
    chart = window._chart_widget
    infinite_lines = [
        item for item in chart.items()
        if isinstance(item, pg.InfiniteLine)
    ]
    assert len(infinite_lines) == 0, (
        f"Expected no InfiniteLine items for 不下单, found {len(infinite_lines)}"
    )
