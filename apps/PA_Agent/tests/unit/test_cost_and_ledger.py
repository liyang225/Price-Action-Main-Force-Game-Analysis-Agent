"""Tests for SessionTokenLedger token thresholds (pricing removed)."""
from __future__ import annotations

import pytest

from pa_agent.ai.deepseek_client import AIUsage
from pa_agent.ai.session_ledger import SessionTokenLedger


def _usage(prompt: int, completion: int, cached: int = 0) -> AIUsage:
    return AIUsage(
        prompt_tokens=prompt,
        cached_prompt_tokens=cached,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )


def test_ledger_accumulates_tokens():
    ledger = SessionTokenLedger(context_window=1_000_000)
    ledger.add(_usage(100, 50))
    ledger.add(_usage(200, 80))
    assert ledger.total_input == 300
    assert ledger.total_output == 130
    assert ledger.context_used == 430


def test_ledger_breakdown_keys():
    ledger = SessionTokenLedger(context_window=10_000)
    ledger.add(_usage(1000, 500))
    bd = ledger.breakdown()
    assert "total_input" in bd
    assert "context_used" in bd
    assert "total_cny" not in bd


def test_yellow_threshold_at_80_pct():
    ledger = SessionTokenLedger(context_window=1000, warn_pct=80.0)
    events: list[tuple[str, dict]] = []
    ledger.threshold_crossed.connect(lambda level, data: events.append((level, data)))
    ledger.add(_usage(800, 0))
    assert len(events) == 1
    assert events[0][0] == "yellow"


def test_red_threshold_at_95_pct():
    ledger = SessionTokenLedger(context_window=1000, warn_pct=80.0)
    events: list[tuple[str, dict]] = []
    ledger.threshold_crossed.connect(lambda level, data: events.append((level, data)))
    ledger.add(_usage(950, 0))
    assert any(e[0] == "red" for e in events)


def test_reset_clears_counters():
    ledger = SessionTokenLedger(context_window=1000)
    ledger.add(_usage(500, 100))
    ledger.reset()
    assert ledger.total_input == 0
    assert ledger.total_output == 0
