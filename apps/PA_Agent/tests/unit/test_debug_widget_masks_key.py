"""Test that DebugWidget masks the plaintext API key in all 4 text areas.

Task 16.5 — pytest-qt test.

Validates: Requirements R17.6, R4.5
"""
from __future__ import annotations

import pytest

# Guard: skip the whole module if PyQt6 is not available
pytest.importorskip("PyQt6")

from pa_agent.util.mask_secret import mask_secret


# ── Fixtures ──────────────────────────────────────────────────────────────────

PLAINTEXT_KEY = "sk-test-1234567890abcdef"


@pytest.fixture
def debug_widget(qtbot):
    """Create a DebugWidget with a known api_key and register it with qtbot."""
    from pa_agent.gui.debug_widget import DebugWidget

    widget = DebugWidget(api_key=PLAINTEXT_KEY)
    qtbot.addWidget(widget)
    return widget


# ── Helpers ───────────────────────────────────────────────────────────────────

def _all_text_areas(widget) -> list[str]:
    """Return the plain-text content of all 4 QTextEdit areas."""
    return [
        widget._system_edit.toPlainText(),
        widget._user_edit.toPlainText(),
        widget._response_edit.toPlainText(),
        widget._validation_edit.toPlainText(),
    ]


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestDebugWidgetMasksKey:
    """DebugWidget must never display the plaintext API key in any text area."""

    def test_plaintext_key_absent_from_all_areas(self, debug_widget):
        """After adding a turn that contains the key, no text area shows it."""
        turn = {
            "label": "Stage1",
            "system_prompt": f"Authorization: Bearer {PLAINTEXT_KEY}",
            "user_prompt": f"api_key={PLAINTEXT_KEY} in user prompt",
            "raw_response": {
                "status": 200,
                "headers": {"Authorization": f"Bearer {PLAINTEXT_KEY}"},
                "body": f"key={PLAINTEXT_KEY}",
                "reasoning_content": f"reasoning with {PLAINTEXT_KEY}",
                "content": "some content",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "request_id": "req-abc123",
            },
            "validation_info": f"validation ok, key={PLAINTEXT_KEY}",
        }
        debug_widget.add_turn(turn)

        for text in _all_text_areas(debug_widget):
            assert PLAINTEXT_KEY not in text, (
                f"Plaintext API key found in displayed text: {text[:200]!r}"
            )

    def test_masked_form_present_in_all_areas(self, debug_widget):
        """After adding a turn that contains the key, all areas show the masked form."""
        expected_mask = mask_secret(PLAINTEXT_KEY)

        turn = {
            "label": "Stage2",
            "system_prompt": f"key={PLAINTEXT_KEY}",
            "user_prompt": f"key={PLAINTEXT_KEY}",
            "raw_response": {
                "content": f"key={PLAINTEXT_KEY}",
            },
            "validation_info": f"key={PLAINTEXT_KEY}",
        }
        debug_widget.add_turn(turn)

        for text in _all_text_areas(debug_widget):
            assert expected_mask in text, (
                f"Expected masked key {expected_mask!r} not found in: {text[:200]!r}"
            )

    def test_no_key_in_text_when_key_not_in_turn(self, debug_widget):
        """Turns without the key should display normally without masking artefacts."""
        turn = {
            "label": "Followup-1",
            "system_prompt": "No secrets here.",
            "user_prompt": "What is the trend?",
            "raw_response": {"content": "Uptrend confirmed."},
            "validation_info": "ok",
        }
        debug_widget.add_turn(turn)

        for text in _all_text_areas(debug_widget):
            assert PLAINTEXT_KEY not in text

    def test_clear_empties_all_areas(self, debug_widget):
        """clear() must empty all 4 text areas."""
        turn = {
            "label": "Stage1",
            "system_prompt": "some text",
            "user_prompt": "some user text",
            "raw_response": {"content": "response"},
            "validation_info": "ok",
        }
        debug_widget.add_turn(turn)
        debug_widget.clear()

        for text in _all_text_areas(debug_widget):
            assert text == "", f"Expected empty text area after clear(), got: {text!r}"
