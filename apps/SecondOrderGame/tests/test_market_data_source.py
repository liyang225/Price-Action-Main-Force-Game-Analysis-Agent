from __future__ import annotations

import pytest

from src.data.fake_client import FakeMarketDataSource
from src.data.futu_client import FutuMarketDataSource
from src.data.models import Bar, CapitalFlow, DragonTiger, MarketSnapshot, NewsItem
from src.data.protocol import DataSourceError, MarketDataSource
from src.data.rate_limiter import FakeClock, RateLimiter


class FakeSdk:
    RET_OK = 0

    class KLType:
        K_120M = "sdk-k-120m"

    class PeriodType:
        DAY = "sdk-day"

    class TradeDateMarket:
        CN = "sdk-cn"


class FakeQuoteContext:
    def request_history_kline(self, **kwargs):
        if kwargs["ktype"] != FakeSdk.KLType.K_120M:
            return 1, "wrong K-line period", None
        return 0, [
            {
                "time_key": "2026-08-10 11:30:00",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 100,
                "turnover": 1050,
            }
        ], None

    def get_capital_flow(self, **kwargs):
        if kwargs["period_type"] != FakeSdk.PeriodType.DAY:
            return 1, "wrong capital-flow period"
        return 0, [
            {
                "capital_flow_item_time": "2026-08-10 00:00:00",
                "main_in_flow": 100,
                "super_in_flow": 50,
                "big_in_flow": 25,
                "mid_in_flow": -10,
                "sml_in_flow": -5,
            }
        ]

    def get_search_news(self, **kwargs):
        return 0, [
            {
                "title": "A market update",
                "publish_time": "2026-08-10 09:00:00",
                "url": "https://example.test/news/1",
                "related_securities": ["SZ.000001"],
            }
        ]

    def request_trading_days(self, **kwargs):
        assert kwargs["market"] == FakeSdk.TradeDateMarket.CN
        return 0, [{"time": "2026-08-11"}, {"time": "2026-08-12"}]

    def get_market_snapshot(self, codes):
        return 0, [
            {"code": code, "change_rate": index + 1.5}
            for index, code in enumerate(codes)
        ]


def test_fake_source_is_the_single_injectable_business_seam() -> None:
    bar = Bar("2026-08-10 11:30:00", 10, 11, 9, 10.5, 100, 1050)
    flow = CapitalFlow("2026-08-10", "SZ.000001", 50, 25, -10, -5, 100)
    news = NewsItem("A market update", "", "https://example.test/news/1", "2026-08-10")
    dragon = DragonTiger("2026-08-10", "SZ.000001", "price move", 100)
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2026-08-10", "2026-08-10"): [bar]},
        capital_flow_data={("SZ.000001", "2026-08-10"): flow},
        news_data={"semiconductor": [news]},
        dragon_tiger_data={("SZ.000001", "2026-08-10"): dragon},
    )

    assert isinstance(source, MarketDataSource)
    assert source.get_kline("SZ.000001", "K_120M", "2026-08-10", "2026-08-10") == [bar]
    assert source.get_capital_flow("SZ.000001", "2026-08-10") == flow
    assert source.search_news("semiconductor") == [news]
    assert source.get_dragon_tiger("SZ.000001", "2026-08-10") == dragon


def test_fake_source_distinguishes_empty_data_from_failures() -> None:
    source = FakeMarketDataSource(failures={"search_news": RuntimeError("offline")})

    assert source.get_kline("SZ.000001", "K_120M", "a", "b") == []
    with pytest.raises(DataSourceError, match="offline"):
        source.search_news("semiconductor")


def test_futu_adapter_normalizes_all_supported_domains_without_connecting() -> None:
    context = FakeQuoteContext()
    source = FutuMarketDataSource(
        quote_context=context,
        futu_module=FakeSdk,
        dragon_tiger_provider=lambda code, day: DragonTiger(day, code, "price move", 100),
    )

    bars = source.get_kline("SZ.000001", "K_120M", "2026-08-10", "2026-08-10")
    flow = source.get_capital_flow("SZ.000001", "2026-08-10")
    news = source.search_news("semiconductor")
    dragon = source.get_dragon_tiger("SZ.000001", "2026-08-10")
    trading_days = source.get_trading_days("CN", "2026-08-01", "2026-08-12")

    assert bars == [Bar("2026-08-10 11:30:00", 10.0, 11.0, 9.0, 10.5, 100, 1050.0)]
    assert flow == CapitalFlow("2026-08-10", "SZ.000001", 50.0, 25.0, -10.0, -5.0, 100.0)
    assert news == [NewsItem("A market update", "", "https://example.test/news/1", "2026-08-10 09:00:00", related_securities=("SZ.000001",))]
    assert dragon == DragonTiger("2026-08-10", "SZ.000001", "price move", 100)
    assert [value.isoformat() for value in trading_days] == ["2026-08-11", "2026-08-12"]


def test_futu_adapter_raises_on_sdk_failure_instead_of_returning_empty_data() -> None:
    class FailingContext(FakeQuoteContext):
        def request_history_kline(self, **kwargs):
            return 1, "OpenD unavailable", None

    source = FutuMarketDataSource(
        quote_context=FailingContext(),
        futu_module=FakeSdk,
    )

    with pytest.raises(DataSourceError, match="OpenD unavailable"):
        source.get_kline("SZ.000001", "K_120M", "a", "b")


def test_futu_adapter_normalizes_batched_constituent_change_rates() -> None:
    source = FutuMarketDataSource(quote_context=FakeQuoteContext(), futu_module=FakeSdk)

    snapshots = source.get_market_snapshots(("SZ.000001", "SH.920005", "SZ.000002"))

    assert snapshots == (
        MarketSnapshot("SZ.000001", 1.5),
        MarketSnapshot("SZ.000002", 2.5),
    )


def test_futu_adapter_derives_change_rate_when_older_opend_omits_the_field() -> None:
    class LegacySnapshotContext(FakeQuoteContext):
        def get_market_snapshot(self, codes):
            return 0, [
                {"code": code, "last_price": 10.5, "prev_close_price": 10.0}
                for code in codes
            ]

    source = FutuMarketDataSource(quote_context=LegacySnapshotContext(), futu_module=FakeSdk)

    assert source.get_market_snapshots(("SZ.000001",)) == (
        MarketSnapshot("SZ.000001", 5.0),
    )


def test_futu_adapter_retries_failed_results_with_injected_time() -> None:
    clock = FakeClock()

    class RecoveringContext(FakeQuoteContext):
        def request_history_kline(self, **kwargs):
            if clock.now() < 1:
                return 1, "temporary OpenD failure", None
            return super().request_history_kline(**kwargs)

    context = RecoveringContext()
    source = FutuMarketDataSource(
        quote_context=context,
        futu_module=FakeSdk,
        retries=2,
        retry_delay=0.5,
        sleep=clock.advance,
        rate_limiter=RateLimiter(10, 30, clock.now),
    )

    assert source.get_kline("SZ.000001", "K_120M", "a", "b")


def test_background_capital_flow_calls_do_not_exhaust_foreground_quote_budget() -> None:
    """Capital-flow collection must not make material preparation rate-limit."""
    source = FutuMarketDataSource(
        quote_context=FakeQuoteContext(),
        futu_module=FakeSdk,
    )

    for _ in range(10):
        assert source.get_capital_flow("SZ.000001", "2026-08-10") is not None

    assert source.get_kline("SZ.000001", "K_120M", "2026-08-10", "2026-08-10")


def test_futu_adapter_rejects_unadapted_dragon_tiger_payloads() -> None:
    source = FutuMarketDataSource(
        quote_context=FakeQuoteContext(),
        futu_module=FakeSdk,
        dragon_tiger_provider=lambda code, day: {"vendor_field": "value"},
    )

    with pytest.raises(DataSourceError, match="must return DragonTiger"):
        source.get_dragon_tiger("SZ.000001", "2026-08-10")
