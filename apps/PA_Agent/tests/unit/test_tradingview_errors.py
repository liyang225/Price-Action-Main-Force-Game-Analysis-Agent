"""TradingView user-facing error messages."""
from __future__ import annotations

from pa_agent.data.tradingview_errors import format_tradingview_fetch_error


def test_empty_data_without_exchange():
    msg = format_tradingview_fetch_error("BTCUSDT", "", empty_data=True)
    assert "未填交易所" in msg
    assert "OANDA" in msg


def test_tvc_xauusd_combo_error():
    msg = format_tradingview_fetch_error("XAUUSD", "TVC", empty_data=True)
    assert "TVC" in msg and "GOLD" in msg


def test_mt5_style_symbol_hint():
    msg = format_tradingview_fetch_error("BTCUSDm", "BINANCE", empty_data=True)
    assert "m 后缀" in msg or "…m" in msg


def test_timeout_message():
    msg = format_tradingview_fetch_error(
        "BTCUSDT", "BINANCE", cause=TimeoutError("Connection timed out")
    )
    assert "超时" in msg
