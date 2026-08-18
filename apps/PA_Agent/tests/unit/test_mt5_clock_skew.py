"""Unit tests: MT5 server clock skew vs local time in forming-bar countdown."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pa_agent.data.bar_close_wait import seconds_until_bar_closes
def test_countdown_inflates_when_local_lags_server_by_3h() -> None:
    """Reproduce Hantec-style skew: bar ts from MT5, now from Windows 3h behind."""
    offset_ms = 3 * 3600 * 1000
    ts_open = 1_700_000_000_000
    # Bar opened 200s ago on server clock
    server_now = ts_open + 200_000
    local_now = server_now - offset_ms

    rem_server = seconds_until_bar_closes(ts_open, "5m", now_ms=server_now)
    rem_local = seconds_until_bar_closes(ts_open, "5m", now_ms=local_now)

    assert rem_server == 100  # 300 - 200
    # Modulo-based countdown is robust to constant ts_open/now offset (same remainder).
    assert rem_local == rem_server


def test_mt5_server_time_ms_prefers_time_msc() -> None:
    from pa_agent.data.mt5 import MT5Source

    src = MT5Source()
    src._connected = True
    src._symbol = "XAUUSD"

    tick = MagicMock()
    tick.time_msc = 1_700_000_123_456
    tick.time = 1_700_000_000

    with patch("MetaTrader5.symbol_info_tick", return_value=tick):
        assert src.server_time_ms() == 1_700_000_123_456


def test_mt5_server_time_ms_falls_back_to_time_seconds() -> None:
    from pa_agent.data.mt5 import MT5Source

    src = MT5Source()
    src._connected = True
    src._symbol = "EURUSD"

    tick = MagicMock()
    tick.time_msc = 0
    tick.time = 1_700_000_000

    with patch("MetaTrader5.symbol_info_tick", return_value=tick):
        assert src.server_time_ms() == 1_700_000_000_000


def test_mt5_server_time_ms_returns_none_when_disconnected() -> None:
    from pa_agent.data.mt5 import MT5Source

    src = MT5Source()
    assert src.server_time_ms() is None

