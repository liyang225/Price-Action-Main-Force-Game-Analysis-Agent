"""E2E smoke test �?symbol switch mid-flight cancels the AI worker.

Task 19.3
"""
from __future__ import annotations

import json
import threading
import time
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


def _make_ctx_slow_stage2(tmp_path):
    """Build a context where stage2 blocks until a cancel token is set."""
    # stage2 call blocks for up to 5 s, but respects the cancel token
    stage2_started = threading.Event()

    def slow_chat(messages, cancel_token=None, **kwargs):
        call_count = slow_chat._call_count
        slow_chat._call_count += 1

        if call_count == 0:
            # Stage 1 �?return immediately
            return _make_reply(VALID_STAGE1)
        else:
            # Stage 2 �?signal that we've started, then block until cancelled
            stage2_started.set()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if cancel_token is not None and cancel_token.is_set():
                    from pa_agent.ai.deepseek_client import CancelledError
                    raise CancelledError("cancelled by token")
                time.sleep(0.05)
            return _make_reply(VALID_STAGE2_ORDER)

    slow_chat._call_count = 0

    mock_client = MagicMock()
    mock_client.stream_chat.side_effect = slow_chat

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

    return ctx, pending_writer, stage2_started


@pytest.mark.e2e
def test_switch_mid_flight_cancels_worker(qtbot, tmp_path):
    """Switching symbol while stage2 is running cancels the worker."""
    from pa_agent.gui.main_window import MainWindow

    ctx, pending_writer, stage2_started = _make_ctx_slow_stage2(tmp_path)

    window = MainWindow(ctx)
    qtbot.addWidget(window)
    window.show()

    window._ctx.settings.general.analysis_bar_count = 5
    window._last_frame_ready_bars = make_newest_first_bars(9, with_forming=True)

    window._on_submit_analysis()
    worker = window._worker
    assert worker is not None, "Worker should have been created"

    # Wait until stage2 has started (so we know the worker is mid-flight)
    assert stage2_started.wait(timeout=5.0), "Stage 2 did not start within 5 s"

    # Trigger symbol switch mid-flight
    window._symbol_combo.setCurrentText("EURUSD")

    # Worker should be cancelled and finish within a reasonable time
    # (the slow_chat loop checks cancel_token every 50 ms)
    finished = worker.wait(6_000)  # 6 s timeout
    assert finished, "Worker did not finish after symbol switch"

    # Tab2 input should be disabled after a symbol switch
    chat_tab = window._tabs.widget(1)
    from PyQt6.QtWidgets import QPlainTextEdit
    input_widgets = chat_tab.findChildren(QPlainTextEdit)
    for widget in input_widgets:
        assert not widget.isEnabled(), (
            "Tab2 input should be disabled after symbol switch"
        )
