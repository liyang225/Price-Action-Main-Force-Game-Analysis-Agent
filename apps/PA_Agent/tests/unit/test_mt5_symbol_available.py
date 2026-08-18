"""Unit tests for MT5 symbol availability check."""
from __future__ import annotations

from pa_agent.data.mt5 import MT5Source


def test_is_symbol_available_empty_name():
    src = MT5Source()
    assert src.is_symbol_available("") is False
    assert src.is_symbol_available("   ") is False


def test_is_symbol_available_when_not_connected_assumes_ok():
    src = MT5Source()
    assert src._connected is False
    assert src.is_symbol_available("XAUUSDm") is True
