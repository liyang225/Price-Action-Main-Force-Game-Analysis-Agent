from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data.models import NewsItem
from src.integration.pa_market_adapter import PAMarketDataAdapter


class PABar:
    def __init__(self, day: int, *, closed: bool = True) -> None:
        self.ts_open = datetime(2026, 8, day, 15, 0).timestamp() * 1000
        self.open = 10.0
        self.high = 11.0
        self.low = 9.5
        self.close = 10.5
        self.volume = 1000
        self.amount = 10_500
        self.closed = closed


class PASource:
    _symbol = "SZ.000001"
    _timeframe = "2h"

    def latest_snapshot(self, n):
        assert n == 1000
        return [PABar(12, closed=False), PABar(11), PABar(10)]


class NewsFallback:
    def __init__(self) -> None:
        self.keywords = []

    def search_news(self, keyword):
        self.keywords.append(keyword)
        return [NewsItem("fallback-news", "summary", "", "2026-08-13")]


def test_pa_market_adapter_converts_closed_bars_oldest_first() -> None:
    result = PAMarketDataAdapter(PASource()).get_kline(
        "SZ.000001", "K_120M", "2026-01-01", "2026-08-12"
    )

    assert [item.time_key[:10] for item in result] == ["2026-08-10", "2026-08-11"]
    assert result[0].turnover == 10_500


def test_pa_market_adapter_routes_non_subscribed_k120m_to_sector_source() -> None:
    source = PASource()
    adapter = PAMarketDataAdapter(source)
    # 非订阅 symbol（板块指数）的 K_120M 走板块行情数据源；未配置时明确报错。
    with pytest.raises(ValueError, match="板块 K_120M"):
        adapter.get_kline("SH.600519", "K_120M", "2026-01-01", "2026-08-12")
    # 订阅 symbol 但周期非 120 分钟仍拒绝。
    source._timeframe = "1h"
    with pytest.raises(ValueError, match="120 分钟"):
        adapter.get_kline("SZ.000001", "K_120M", "2026-01-01", "2026-08-12")


def test_pa_market_adapter_uses_optional_news_fallback_without_api_key_contract() -> None:
    fallback = NewsFallback()
    adapter = PAMarketDataAdapter(PASource(), news_fallback_provider=fallback)

    assert adapter.search_news("SZ.000001") == [
        NewsItem("fallback-news", "summary", "", "2026-08-13")
    ]
    assert fallback.keywords == ["SZ.000001"]


def test_pa_market_adapter_normalizes_embedded_news_summary_without_opening_url() -> None:
    source = PASource()
    source.search_news = lambda _keyword: [
        {
            "title": "Company filing",
            "summary": "Embedded Futu summary",
            "url": "https://example.test/not-opened",
            "publish_time": "2026-08-13 10:00:00",
        }
    ]

    result = PAMarketDataAdapter(source).search_news("Kweichow Moutai")

    assert result == [
        NewsItem(
            "Company filing",
            "Embedded Futu summary",
            "https://example.test/not-opened",
            "2026-08-13 10:00:00",
            "Futu OpenD",
        )
    ]


def test_pa_market_adapter_normalizes_epoch_to_china_time() -> None:
    source = PASource()
    bar = PABar(12)
    bar.ts_open = datetime(2026, 8, 12, tzinfo=timezone.utc).timestamp() * 1000
    source.latest_snapshot = lambda _n: [bar]

    result = PAMarketDataAdapter(source).get_kline(
        "SZ.000001", "K_120M", "2026-01-01", "2026-08-12"
    )

    assert result[0].time_key == "2026-08-12 08:00:00"


def test_pa_market_adapter_delegates_sector_daily_bars_and_calendar():
    class SectorSource:
        def get_kline(self, code, ktype, start, end):
            assert (code, ktype) == ("SH.BK0001", "K_DAY")
            return ["sector-bar"]

        def get_trading_days(self, market, start, end):
            return ["2026-08-12"]

    adapter = PAMarketDataAdapter(PASource(), sector_market_source=SectorSource())

    assert adapter.get_kline("SH.BK0001", "K_DAY", "2026-08-01", "2026-08-12") == ["sector-bar"]
    assert adapter.get_trading_days("CN", "2026-08-01", "2026-08-12") == ["2026-08-12"]


def test_pa_market_adapter_delegates_capital_flow_to_sector_source() -> None:
    class SectorSource:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def get_capital_flow(self, code: str, date: str) -> dict[str, float]:
            self.calls.append((code, date))
            return {"main_in_flow": 88.0}

    sector = SectorSource()
    adapter = PAMarketDataAdapter(PASource(), sector_market_source=sector)

    assert adapter.get_capital_flow("SH.LIST0022", "2026-08-14") == {
        "main_in_flow": 88.0
    }
    assert sector.calls == [("SH.LIST0022", "2026-08-14")]


def test_pa_market_adapter_delegates_sector_capital_flow_history() -> None:
    class SectorSource:
        def __init__(self) -> None:
            self.names: list[str] = []

        def get_sector_capital_flow_history(self, name: str):
            self.names.append(name)
            return ["sector-flow"]

    sector = SectorSource()
    adapter = PAMarketDataAdapter(PASource(), sector_market_source=sector)

    assert adapter.get_sector_capital_flow_history("半导体") == ["sector-flow"]
    assert sector.names == ["半导体"]


def test_pa_market_adapter_delegates_sector_member_snapshots() -> None:
    class SectorSource:
        def get_market_snapshots(self, codes):
            return [(code, 1.0) for code in codes]

    adapter = PAMarketDataAdapter(PASource(), sector_market_source=SectorSource())

    assert adapter.get_market_snapshots(("SZ.000001", "SZ.000002")) == [
        ("SZ.000001", 1.0),
        ("SZ.000002", 1.0),
    ]


def test_pa_market_adapter_delegates_dragon_tiger_to_sector_source() -> None:
    from src.data.models import DragonTiger

    class SectorSource:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def get_dragon_tiger(self, code: str, date: str) -> DragonTiger:
            self.calls.append((code, date))
            return DragonTiger(date, code, "price move", 100.0)

    sector = SectorSource()
    adapter = PAMarketDataAdapter(PASource(), sector_market_source=sector)

    result = adapter.get_dragon_tiger("SZ.000001", "2026-08-10")

    assert sector.calls == [("SZ.000001", "2026-08-10")]
    assert result.code == "SZ.000001"
    assert result.net_buy_amount == 100.0


def test_pa_market_adapter_delegates_limit_pool_to_sector_source() -> None:
    from src.data.models import LimitPoolRecord

    class SectorSource:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_limit_pool(self, date: str) -> tuple[LimitPoolRecord, ...]:
            self.calls.append(date)
            return (LimitPoolRecord(date, "SZ.000001", 3, "rise"),)

    sector = SectorSource()
    adapter = PAMarketDataAdapter(PASource(), sector_market_source=sector)

    result = adapter.get_limit_pool("2026-08-10")

    assert sector.calls == ["2026-08-10"]
    assert result[0].code == "SZ.000001"
    assert result[0].limit_streak == 3
    assert result[0].direction == "rise"


def test_pa_market_adapter_defers_sector_code_validation_to_source() -> None:
    source = PASource()
    source.get_sector_constituents = lambda code: (
        ("HK.00001",) if code == "HK.HSI Constituent" else ()
    )
    adapter = PAMarketDataAdapter(source)

    adapter.validate_sector_code("HK.HSI Constituent")
    with pytest.raises(ValueError, match="没有返回板块成分股"):
        adapter.validate_sector_code("US.ANYTHING")


class _FutuNewsSource:
    _symbol = "SZ.000001"
    _timeframe = "2h"

    def __init__(self, rows) -> None:
        self._rows = rows

    def latest_snapshot(self, n):
        return []

    def search_news(self, keyword):
        return self._rows


class _TavilyNewsSource:
    def __init__(self, items) -> None:
        self._items = items

    def search_news(self, keyword):
        return self._items


def test_pa_market_adapter_backfills_futu_titles_with_tavily_text() -> None:
    futu = _FutuNewsSource(
        [
            {
                "title": "芯片板块大涨",
                "url": "https://futu.test/1",
                "publish_time": "2026-08-13 10:00:00",
                "related_securities": ["SZ.000001"],
            },
            {
                "title": "某公司例行公告",
                "url": "https://futu.test/2",
                "publish_time": "2026-08-13 09:00:00",
            },
        ]
    )
    tavily = _TavilyNewsSource(
        [
            NewsItem("芯片板块大涨", "芯片需求增长，政策支持扩产", "https://tavily.test/1", "2026-08-13"),
            NewsItem("独立新闻", "独立新闻正文内容", "https://tavily.test/2", "2026-08-13"),
        ]
    )
    adapter = PAMarketDataAdapter(futu, news_fallback_provider=tavily)

    result = adapter.search_news("半导体")

    by_title = {item.title: item for item in result}
    # 标题匹配 → 正文回填到富途条目，保留富途的关联证券与链接
    matched = by_title["芯片板块大涨"]
    assert matched.snippet == "芯片需求增长，政策支持扩产"
    assert matched.url == "https://futu.test/1"
    assert matched.related_securities == ("SZ.000001",)
    # Tavily 独有条目作为独立新闻保留
    assert by_title["独立新闻"].snippet == "独立新闻正文内容"
    # 无正文的富途条目排在有正文条目之后
    assert result[-1].title == "某公司例行公告"


def test_pa_market_adapter_caps_news_batch_at_eighteen_with_text_first() -> None:
    futu = _FutuNewsSource(
        [
            {"title": f"futu-{index}", "url": f"https://futu.test/{index}", "publish_time": "2026-08-13"}
            for index in range(25)
        ]
    )
    tavily = _TavilyNewsSource(
        [
            NewsItem(f"tavily-{index}", f"text-{index}", f"https://tavily.test/{index}", "2026-08-13")
            for index in range(25)
        ]
    )
    adapter = PAMarketDataAdapter(futu, news_fallback_provider=tavily)

    result = adapter.search_news("半导体")

    assert len(result) == 18
    # 有正文的条目优先保留，保证情绪分与大模型预分析有内容
    assert all(item.snippet for item in result)


def test_adapter_closes_its_sector_market_source() -> None:
    closed: list[bool] = []

    class PASource:
        def latest_snapshot(self, _count):
            return []

    class SectorSource:
        def close(self):
            closed.append(True)

    PAMarketDataAdapter(PASource(), sector_market_source=SectorSource()).close()

    assert closed == [True]


class _ConstituentsSectorSource:
    def get_sector_constituents(self, sector_code: str):
        return ("SH.600000", "SZ.000001") if sector_code == "BK0475" else ()


def test_adapter_forwards_sector_constituents_to_sector_source() -> None:
    adapter = PAMarketDataAdapter(
        PASource(), sector_market_source=_ConstituentsSectorSource()
    )
    assert adapter.get_sector_constituents("BK0475") == ("SH.600000", "SZ.000001")
    assert adapter.get_sector_constituents("BK9999") == ()


def test_adapter_raises_when_sector_source_has_no_constituents() -> None:
    adapter = PAMarketDataAdapter(PASource())  # 无板块行情数据源
    with pytest.raises(NotImplementedError, match="成分股"):
        adapter.get_sector_constituents("BK0475")
