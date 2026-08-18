"""TradingView HK codes and name alias lookup."""
from __future__ import annotations

import pytest

from pa_agent.data.market_defaults import (
    equity_tv_auto_probe_plan,
    normalize_hk_tv_code,
    resolve_tv_fetch_pair,
    resolve_tv_pair,
)
from pa_agent.data.tradingview import TradingViewSource
from pa_agent.data.tv_symbol_lookup import (
    TvSymbolNotFoundError,
    lookup_tv_symbol_by_name,
    resolve_tv_symbol_name,
)


def test_normalize_hk_keeps_leading_zeros() -> None:
    assert normalize_hk_tv_code("00988") == "00988"
    assert normalize_hk_tv_code("0700") == "0700"
    assert normalize_hk_tv_code("1810") == "1810"


def test_xiaomi_name_resolves_to_hkex_1810() -> None:
    assert lookup_tv_symbol_by_name("小米集团") == ("HKEX", "1810")
    assert resolve_tv_symbol_name("小米") == ("HKEX", "1810")


def test_resolve_tv_pair_by_name() -> None:
    ex, sym, adjusted = resolve_tv_pair("", "小米集团")
    assert ex == "HKEX" and sym == "1810" and adjusted is True


def test_resolve_tv_fetch_pair_for_api_only() -> None:
    # Auto exchange: keep user text; probe happens in RefreshLoop.
    assert resolve_tv_fetch_pair("", "小米集团") == ("", "小米集团")
    assert resolve_tv_fetch_pair("HKEX", "1810") == ("HKEX", "1810")
    # Manual exchange + name: resolve for API only, UI text unchanged elsewhere.
    assert resolve_tv_fetch_pair("HKEX", "小米集团") == ("HKEX", "1810")


def test_bilibili_aliases() -> None:
    assert lookup_tv_symbol_by_name("哔哩哔哩") == ("HKEX", "9626")
    assert lookup_tv_symbol_by_name("B站") == ("HKEX", "9626")
    assert lookup_tv_symbol_by_name("哔哩哔哩-W") == ("HKEX", "9626")
    assert lookup_tv_symbol_by_name("BILI") == ("NASDAQ", "BILI")


def test_subscribe_keeps_user_symbol_text() -> None:
    src = TradingViewSource()
    src.set_exchange("")
    src.subscribe("小米集团", "1d")
    assert src._symbol == "小米集团"


def test_resolve_tv_pair_hk_auto() -> None:
    ex, sym, adjusted = resolve_tv_pair("", "1810")
    assert ex == "" and sym == "1810" and adjusted is False
    assert equity_tv_auto_probe_plan("1810") == [("HKEX", "1810")]


def test_unknown_name_raises() -> None:
    with pytest.raises(TvSymbolNotFoundError):
        resolve_tv_symbol_name("不存在的公司_xyz")
