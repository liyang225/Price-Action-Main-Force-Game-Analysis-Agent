"""Tests for ConversationWidget token indicator thresholds.

Task 15.5 — pytest-qt tests:
- Progress bar turns yellow at 80% context usage.
- Progress bar turns red at 95% context usage.
- QMessageBox.warning is shown exactly once at 95%.

Validates: Requirements R10.5, R15.4
"""
from __future__ import annotations

import pytest

from PyQt6.QtWidgets import QProgressBar

# Guard: skip entire module if PyQt6 display is unavailable
pytest.importorskip("PyQt6.QtWidgets")


@pytest.fixture
def widget(qtbot):
    """Create a ConversationWidget and register it with qtbot."""
    from pa_agent.gui.conversation_widget import ConversationWidget

    w = ConversationWidget()
    qtbot.addWidget(w)
    w.show()
    return w


def _make_data(context_used: int, context_window: int = 1_000_000) -> dict:
    """Build a minimal token-display data dict."""
    return {
        "context_used": context_used,
        "context_window": context_window,
        "total_input": context_used,
        "total_cached_input": 0,
        "total_output": 0,
    }


# ── Yellow threshold (80%) ────────────────────────────────────────────────────

def test_progress_bar_yellow_at_80_pct(widget, monkeypatch):
    """Progress bar stylesheet should switch to yellow at exactly 80%."""
    # Patch QMessageBox.warning to prevent any dialog from blocking
    monkeypatch.setattr(
        "pa_agent.gui.conversation_widget.QMessageBox.warning",
        lambda *args, **kwargs: None,
    )

    widget.update_token_display(_make_data(800_000))  # 80%

    style = widget._progress_bar.styleSheet()
    assert "C0913C" in style.upper(), (
        f"Expected yellow (#C0913C) in stylesheet at 80%, got: {style!r}"
    )


def test_progress_bar_not_yellow_below_80_pct(widget, monkeypatch):
    """Progress bar should have no special colour below 80%."""
    monkeypatch.setattr(
        "pa_agent.gui.conversation_widget.QMessageBox.warning",
        lambda *args, **kwargs: None,
    )

    widget.update_token_display(_make_data(799_999))  # just under 80%

    style = widget._progress_bar.styleSheet()
    style_upper = style.upper()
    assert "C0913C" not in style_upper
    assert "EF4444" not in style_upper


# ── Red threshold (95%) ───────────────────────────────────────────────────────

def test_progress_bar_red_at_95_pct(widget, monkeypatch):
    """Progress bar stylesheet should switch to red at exactly 95%."""
    warning_calls: list = []
    monkeypatch.setattr(
        "pa_agent.gui.conversation_widget.QMessageBox.warning",
        lambda *args, **kwargs: warning_calls.append(args),
    )

    widget.update_token_display(_make_data(950_000))  # 95%

    style = widget._progress_bar.styleSheet()
    assert "FF4757" in style, (
        f"Expected red (#FF4757) in stylesheet at 95%, got: {style!r}"
    )


def test_qmessagebox_shown_once_at_95_pct(widget, monkeypatch):
    """QMessageBox.warning should be called exactly once when crossing 95%."""
    warning_calls: list = []
    monkeypatch.setattr(
        "pa_agent.gui.conversation_widget.QMessageBox.warning",
        lambda *args, **kwargs: warning_calls.append(args),
    )

    # First call at 95% — should trigger warning
    widget.update_token_display(_make_data(950_000))
    assert len(warning_calls) == 1, "Expected exactly 1 warning at 95%"

    # Second call still at 95% — should NOT trigger again (one-time flag)
    widget.update_token_display(_make_data(960_000))
    assert len(warning_calls) == 1, "Warning should only fire once"


def test_qmessagebox_not_shown_at_80_pct(widget, monkeypatch):
    """QMessageBox.warning should NOT be called at 80% (yellow only)."""
    warning_calls: list = []
    monkeypatch.setattr(
        "pa_agent.gui.conversation_widget.QMessageBox.warning",
        lambda *args, **kwargs: warning_calls.append(args),
    )

    widget.update_token_display(_make_data(800_000))  # 80%

    assert len(warning_calls) == 0, "No warning dialog expected at 80%"


# ── Red overrides yellow ──────────────────────────────────────────────────────

def test_red_overrides_yellow(widget, monkeypatch):
    """When usage jumps from 80% to 95%, bar should be red (not yellow)."""
    monkeypatch.setattr(
        "pa_agent.gui.conversation_widget.QMessageBox.warning",
        lambda *args, **kwargs: None,
    )

    widget.update_token_display(_make_data(800_000))  # 80% → yellow
    widget.update_token_display(_make_data(950_000))  # 95% → red

    style = widget._progress_bar.styleSheet()
    assert "FF4757" in style, "Expected red at 95% even after yellow at 80%"


# ── Progress bar value ────────────────────────────────────────────────────────

def test_progress_bar_value_matches_percentage(widget, monkeypatch):
    """Progress bar integer value should match the rounded percentage."""
    monkeypatch.setattr(
        "pa_agent.gui.conversation_widget.QMessageBox.warning",
        lambda *args, **kwargs: None,
    )

    widget.update_token_display(_make_data(342_000))  # 34.2%

    assert widget._progress_bar.value() == 34


# ── Clear resets warning flag ─────────────────────────────────────────────────

def test_clear_resets_red_warning_flag(widget, monkeypatch):
    """After clear(), the 95% warning should fire again on next update."""
    warning_calls: list = []
    monkeypatch.setattr(
        "pa_agent.gui.conversation_widget.QMessageBox.warning",
        lambda *args, **kwargs: warning_calls.append(args),
    )

    widget.update_token_display(_make_data(950_000))  # fires once
    assert len(warning_calls) == 1

    widget.clear()  # resets flag

    widget.update_token_display(_make_data(950_000))  # should fire again
    assert len(warning_calls) == 2, "Warning should fire again after clear()"
