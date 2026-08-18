from __future__ import annotations

import sys
import types
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import pa_agent.data.futu_source as futu_source_module
from pa_agent.config.settings import Settings
from pa_agent.data.base import DataSourceTransientError
from pa_agent.data.futu_source import FutuSource, normalize_futu_symbol


def test_normalize_futu_symbol() -> None:
    assert normalize_futu_symbol("600519") == "SH.600519"
    assert normalize_futu_symbol("000001") == "SZ.000001"
    assert normalize_futu_symbol("600519.SH") == "SH.600519"
    assert normalize_futu_symbol("hk.700") == "HK.00700"
    assert normalize_futu_symbol("US:AAPL") == "US.AAPL"
    assert normalize_futu_symbol("159732") == "SZ.159732"
    assert normalize_futu_symbol("159732", exchange="SSE") == "SZ.159732"
    assert normalize_futu_symbol("600519", exchange="SZSE") == "SH.600519"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("600000", "SH.600000"),
        ("688001", "SH.688001"),
        ("510300", "SH.510300"),
        ("501018", "SH.501018"),
        ("508000", "SH.508000"),
        ("000001", "SZ.000001"),
        ("300750", "SZ.300750"),
        ("160000", "SZ.160000"),
        ("180101", "SZ.180101"),
        ("159915", "SZ.159915"),
        ("920001", "BJ.920001"),
    ],
)
def test_normalize_futu_symbol_covers_a_share_product_families(
    code: str, expected: str
) -> None:
    assert normalize_futu_symbol(code) == expected


def test_normalize_futu_symbol_known_families_override_conflicting_exchange() -> None:
    assert normalize_futu_symbol("159915", exchange="SSE") == "SZ.159915"
    assert normalize_futu_symbol("510300", exchange="SZSE") == "SH.510300"
    assert normalize_futu_symbol("920001", exchange="SSE") == "BJ.920001"


def test_futu_source_uses_configured_opend_and_maps_period(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    class FakeContext:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def get_cur_kline(self, code, num, ktype, autype):
            calls.append((code, num, ktype, autype))
            return 0, pd.DataFrame([{
                "time_key": "2024-01-03 10:30:00", "open": 10.0, "high": 11.0,
                "low": 9.0, "close": 10.5, "volume": 100, "turnover": 1050,
            }])

        def get_market_snapshot(self, codes):
            calls.append(("snapshot", codes))
            return 0, pd.DataFrame([{
                "last_price": 10.5, "open_price": 10.0, "high_price": 11.0,
                "low_price": 9.0, "turnover": 1050, "turnover_rate": 1.2,
                "change_rate": 2.5, "volume": 100,
            }])

        def subscribe(self, codes, subtypes, subscribe_push):
            calls.append(("subscribe", codes, subtypes, subscribe_push))
            return 0, None

        def unsubscribe(self, codes, subtypes):
            calls.append(("unsubscribe", codes, subtypes))
            return 0, None

        def close(self):
            calls.append("closed")

    fake_futu = types.SimpleNamespace(
        OpenQuoteContext=FakeContext, RET_OK=0,
        KLType=types.SimpleNamespace(K_15M="K_15M"),
        SubType=types.SimpleNamespace(K_15M="K_15M"),
        AuType=types.SimpleNamespace(QFQ="qfq"),
    )
    monkeypatch.setitem(sys.modules, "futu", fake_futu)
    source = FutuSource(Settings(futu={"opend_host": "10.0.0.7", "opend_port": 12345}))
    source.connect()
    source.subscribe("600519", "15m")
    bars = source.latest_snapshot(1)
    source.disconnect()

    assert calls[0] == {"host": "10.0.0.7", "port": 12345}
    assert calls[1] == ("subscribe", ["SH.600519"], ["K_15M"], False)
    assert calls[2] == ("SH.600519", 120, "K_15M", "qfq")
    assert calls[3] == ("snapshot", ["SH.600519"])
    assert bars[0].close == 10.5
    assert bars[0].closed is True
    assert bars[0].amount == 1050
    assert bars[0].ts_open > 0
    assert datetime.fromtimestamp(bars[0].ts_open / 1000, tz=ZoneInfo("Asia/Shanghai")).strftime(
        "%H:%M"
    ) == "10:30"
    assert bars[0].timestamp_is_close is True
    assert source.latest_market_summary() == {
        "last_price": 10.5, "open_price": 10.0, "high_price": 11.0,
        "low_price": 9.0, "volume": 100.0, "turnover": 1050.0, "turnover_rate": 1.2,
        "change_rate": 2.5,
    }
    assert calls[-1] == "closed"


def test_futu_future_close_label_remains_open() -> None:
    frame = pd.DataFrame(
        [{
            "time_key": "2099-01-03 15:00:00",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 100,
        }]
    )

    bars = futu_source_module._df_to_bars_newest_first(frame, 1, "CN", "2h")

    assert bars[0].closed is False


def test_futu_source_surfaces_opend_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeContext:
        def __init__(self, **kwargs):
            pass

        def get_cur_kline(self, *args, **kwargs):
            return -1, "OpenD disconnected"

        def subscribe(self, codes, subtypes, subscribe_push):
            return 0, None

    fake_futu = types.SimpleNamespace(
        OpenQuoteContext=FakeContext, RET_OK=0,
        KLType=types.SimpleNamespace(K_DAY="K_DAY"),
        SubType=types.SimpleNamespace(K_DAY="K_DAY"),
        AuType=types.SimpleNamespace(QFQ="qfq"),
    )
    monkeypatch.setitem(sys.modules, "futu", fake_futu)
    source = FutuSource()
    source.connect()
    source.subscribe("US.AAPL", "1d")
    with pytest.raises(DataSourceTransientError, match="OpenD disconnected"):
        source.latest_snapshot(1)


def test_futu_source_switches_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    class FakeContext:
        def __init__(self, **kwargs):
            pass

        def subscribe(self, codes, subtypes, subscribe_push):
            calls.append(("subscribe", codes, subtypes, subscribe_push))
            return 0, None

        def unsubscribe(self, codes, subtypes):
            calls.append(("unsubscribe", codes, subtypes))
            return 0, None

    fake_futu = types.SimpleNamespace(
        OpenQuoteContext=FakeContext, RET_OK=0,
        SubType=types.SimpleNamespace(K_5M="K_5M", K_15M="K_15M"),
    )
    monkeypatch.setitem(sys.modules, "futu", fake_futu)
    monkeypatch.setattr(futu_source_module, "_MIN_OPEND_SUBSCRIPTION_SECONDS", 0)
    source = FutuSource()
    source.connect()
    source.subscribe("600519", "15m")
    source.subscribe("000001", "5m")
    source.unsubscribe()

    assert calls == [
        ("subscribe", ["SH.600519"], ["K_15M"], False),
        ("unsubscribe", ["SH.600519"], ["K_15M"]),
        ("subscribe", ["SZ.000001"], ["K_5M"], False),
        ("unsubscribe", ["SZ.000001"], ["K_5M"]),
    ]


def test_futu_source_derives_change_rate_from_previous_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeContext:
        def get_market_snapshot(self, _codes):
            return 0, pd.DataFrame([{
                "last_price": 101.0,
                "prev_close_price": 100.0,
            }])

    fake_futu = types.SimpleNamespace(RET_OK=0)
    monkeypatch.setitem(sys.modules, "futu", fake_futu)
    source = FutuSource()
    source._context = FakeContext()
    source._symbol = "SH.600519"

    source._refresh_market_summary()

    assert source.latest_market_summary() == {
        "last_price": 101.0,
        "change_rate": 1.0,
    }


def test_futu_source_uses_returned_news_summary_without_opening_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeContext:
        def get_search_news(self, *, keyword, max_count):
            assert keyword == "贵州茅台"
            assert max_count == 100
            return 0, pd.DataFrame([
                {
                    "title": "公司公告",
                    "summary": "公告摘要正文",
                    "url": "https://example.invalid/article",
                    "publish_time": "2026-08-13 10:00:00",
                }
            ])

    monkeypatch.setitem(sys.modules, "futu", types.SimpleNamespace(RET_OK=0))
    source = FutuSource()
    source._connected = True
    source._context = FakeContext()

    assert source.search_news("贵州茅台") == [
        {
            "title": "公司公告",
            "summary": "公告摘要正文",
            "url": "https://example.invalid/article",
            "publish_time": "2026-08-13 10:00:00",
            "source": "Futu OpenD",
            "related_securities": (),
        }
    ]


def test_futu_source_gets_sector_constituents_without_format_assumptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeContext:
        def get_plate_stock(self, sector_code):
            assert sector_code == "HK.HSI Constituent"
            return 0, pd.DataFrame([{"code": "HK.00001"}, {"code": "HK.00700"}])

    monkeypatch.setitem(sys.modules, "futu", types.SimpleNamespace(RET_OK=0))
    source = FutuSource()
    source._connected = True
    source._context = FakeContext()

    assert source.get_sector_constituents("HK.HSI Constituent") == (
        "HK.00001",
        "HK.00700",
    )


def test_futu_source_surfaces_sector_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeContext:
        def get_plate_stock(self, _sector_code):
            return -1, "unknown plate code"

    monkeypatch.setitem(sys.modules, "futu", types.SimpleNamespace(RET_OK=0))
    source = FutuSource()
    source._connected = True
    source._context = FakeContext()

    with pytest.raises(DataSourceTransientError, match="unknown plate code"):
        source.get_sector_constituents("US.ANYTHING")
