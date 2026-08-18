"""WebSocket lifecycle for TradingViewSource.

These guard the fixes for tvDatafeed 2.x's leaking WebSocket: the source must
close the socket after every fetch and when re-subscribing, so half-open
connections don't accumulate (which trips TradingView rate limiting) and a
symbol/timeframe switch can abort an in-flight request.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pa_agent.data.tradingview import TradingViewSource


def _make_source_with_mock_tv() -> tuple[TradingViewSource, MagicMock]:
    src = TradingViewSource()
    tv = MagicMock()
    tv.ws = MagicMock()
    src._tv = tv
    src._connected = True
    return src, tv


def test_close_tv_socket_closes_and_clears() -> None:
    src, tv = _make_source_with_mock_tv()
    ws = tv.ws

    src._close_tv_socket()

    ws.close.assert_called_once()
    assert tv.ws is None


def test_close_tv_socket_noop_when_no_socket() -> None:
    src, tv = _make_source_with_mock_tv()
    tv.ws = None
    # Should not raise even though there is no live socket.
    src._close_tv_socket()


def test_close_tv_socket_swallows_close_error() -> None:
    src, tv = _make_source_with_mock_tv()
    tv.ws.close.side_effect = RuntimeError("already closed")
    # Errors during close must not propagate.
    src._close_tv_socket()
    assert tv.ws is None


def test_disconnect_closes_socket() -> None:
    src, tv = _make_source_with_mock_tv()
    ws = tv.ws

    src.disconnect()

    ws.close.assert_called_once()
    assert src._tv is None
    assert src._connected is False


def test_subscribe_aborts_inflight_socket() -> None:
    src, tv = _make_source_with_mock_tv()
    ws = tv.ws

    src.subscribe("XAUUSD", "15m")

    # The in-flight socket is closed so the switch takes effect immediately.
    ws.close.assert_called_once()
    assert src._symbol == "XAUUSD"
    assert src._timeframe == "15m"


def test_fetch_closes_socket_after_each_call() -> None:
    src, tv = _make_source_with_mock_tv()
    ws = tv.ws
    df = MagicMock()
    df.empty = False
    tv.get_hist.return_value = df

    out = src._fetch_hist_with_retry(
        symbol="XAUUSD", exchange="OANDA", interval=object(), n_bars=10
    )

    assert out is df
    ws.close.assert_called_once()
    assert tv.ws is None


def test_subscribe_rejects_unknown_timeframe() -> None:
    src, _tv = _make_source_with_mock_tv()
    with pytest.raises(ValueError):
        src.subscribe("XAUUSD", "7m")
