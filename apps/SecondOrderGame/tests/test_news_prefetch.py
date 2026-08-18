from __future__ import annotations

import json
from threading import Event, Lock

import pytest

from src.data import NewsPrefetchTask, TavilyNewsProvider
from src.data.daily_cache import DailyMaterialCache
from src.data.fake_client import FakeMarketDataSource
from src.data.futu_client import FutuMarketDataSource
from src.data.models import NewsItem
from src.data.rate_limiter import FakeClock, RateLimiter


def test_twenty_sector_round_finishes_within_sixty_seconds_and_caches_every_result(
    tmp_path, daily_clock
) -> None:
    clock = FakeClock()
    sectors = tuple(f"sector-{index:02d}" for index in range(20))
    news_by_sector = {
        sector: [
            NewsItem(
                title=f"{sector} update",
                snippet="",
                url=f"https://example.test/{sector}",
                published_date="2026-08-11 09:00:00",
                source="Futu",
            )
        ]
        for sector in sectors
    }
    source = FakeMarketDataSource(news_data=news_by_sector)
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)
    task = NewsPrefetchTask(
        source,
        cache,
        rate_limiter=RateLimiter(10, 30, clock.now),
        clock=clock.now,
        sleep=clock.advance,
    )

    report = task.run_round(sectors)

    assert report.attempted_sectors == sectors
    assert report.cached_sectors == sectors
    assert report.failures == ()
    assert report.elapsed_seconds == 57
    assert report.elapsed_seconds < 60
    assert cache.snapshot().materials["news"] == {
        sector: tuple(news_by_sector[sector]) for sector in sectors
    }


def test_rate_limit_window_reopens_and_the_same_round_continues(
    tmp_path, daily_clock
) -> None:
    clock = FakeClock()

    class RecordingFakeMarketDataSource(FakeMarketDataSource):
        def __init__(self) -> None:
            super().__init__()
            self.call_times: list[float] = []

        def search_news(self, keyword: str) -> list[NewsItem]:
            self.call_times.append(clock.now())
            return super().search_news(keyword)

    source = RecordingFakeMarketDataSource()
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)
    task = NewsPrefetchTask(
        source,
        cache,
        rate_limiter=RateLimiter(10, 30, clock.now),
        interval_seconds=0,
        clock=clock.now,
        sleep=clock.advance,
    )
    sectors = tuple(f"sector-{index:02d}" for index in range(20))

    report = task.run_round(sectors)

    assert source.call_times == [0.0] * 10 + [30.0] * 10
    assert report.cached_sectors == sectors
    assert report.failures == ()


def test_start_runs_repeated_rounds_off_the_caller_thread_and_stop_joins_cleanly(
    tmp_path, daily_clock
) -> None:
    reached_two_rounds = Event()
    counter_lock = Lock()

    class CountingFakeMarketDataSource(FakeMarketDataSource):
        def __init__(self) -> None:
            super().__init__()
            self.call_count = 0

        def search_news(self, keyword: str) -> list[NewsItem]:
            with counter_lock:
                self.call_count += 1
                if self.call_count >= 2:
                    reached_two_rounds.set()
            return super().search_news(keyword)

    source = CountingFakeMarketDataSource()
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)
    task = NewsPrefetchTask(
        source,
        cache,
        rate_limiter=RateLimiter(1000, 30),
        interval_seconds=0,
        round_interval_seconds=0,
    )

    task.start(lambda: ("semiconductors",))

    assert reached_two_rounds.wait(timeout=1)
    assert task.is_running
    task.stop(timeout=1)
    assert not task.is_running
    assert task.background_error is None


def test_task_status_exposes_last_round_without_exposing_mutable_internals(
    tmp_path, daily_clock
) -> None:
    source = FakeMarketDataSource(
        news_data={
            "半导体": (
                NewsItem(
                    "芯片更新",
                    "",
                    "https://example.test/chip",
                    "2026-08-11 10:00:00",
                ),
            )
        }
    )
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)
    task = NewsPrefetchTask(source, cache, interval_seconds=0)

    task.run_round(("半导体",))

    status = task.status()
    assert status["state"] == "stopped"
    assert status["round_count"] == 1
    assert status["last_round"]["attempted_sectors"] == ["半导体"]
    assert status["last_round"]["cached_sectors"] == ["半导体"]
    assert status["last_round"]["failures"] == []


def test_one_fake_drives_empty_and_failed_futu_searches_through_tavily_fallback(
    tmp_path, daily_clock
) -> None:
    clock = FakeClock()
    empty_fallback = NewsItem(
        "Empty result supplement",
        "",
        "https://example.test/tavily/empty",
        "2026-08-11 09:10:00",
        source="Tavily",
    )
    failure_fallback = NewsItem(
        "Failure supplement",
        "",
        "https://example.test/tavily/failure",
        "2026-08-11 09:11:00",
        source="Tavily",
    )
    later_futu_result = NewsItem(
        "Later Futu result",
        "",
        "https://example.test/futu/later",
        "2026-08-11 09:12:00",
        source="Futu",
    )
    source = FakeMarketDataSource(
        news_data={"futu-empty": (), "later-sector": (later_futu_result,)},
        fallback_news_data={
            "futu-empty": (empty_fallback,),
            "futu-failed": (failure_fallback,),
        },
        failures={
            ("search_news", "futu-failed"): "Futu unavailable",
            ("search_news", "unavailable"): "all sources unavailable",
        },
    )
    cache = DailyMaterialCache(tmp_path / "archives", clock=daily_clock)
    task = NewsPrefetchTask(
        source,
        cache,
        rate_limiter=RateLimiter(10, 30, clock.now),
        interval_seconds=0,
        clock=clock.now,
        sleep=clock.advance,
    )

    report = task.run_round(
        ("futu-empty", "futu-failed", "unavailable", "later-sector")
    )

    assert report.cached_sectors == (
        "futu-empty",
        "futu-failed",
        "later-sector",
    )
    assert tuple(failure.sector for failure in report.failures) == ("unavailable",)
    assert cache.snapshot().materials["news"] == {
        "futu-empty": (empty_fallback,),
        "futu-failed": (failure_fallback,),
        "later-sector": (later_futu_result,),
    }


@pytest.mark.parametrize(
    "futu_result",
    [
        (0, []),
        (1, "OpenD news search failed"),
    ],
    ids=["empty", "failure"],
)
def test_futu_adapter_uses_its_internal_tavily_provider_for_news_gaps(
    futu_result,
) -> None:
    class FakeSdk:
        RET_OK = 0

    class NewsContext:
        def get_search_news(self, **kwargs):
            return futu_result

    tavily_item = NewsItem(
        "Tavily supplement",
        "",
        "https://example.test/tavily/supplement",
        "2026-08-11 09:15:00",
        source="Tavily",
    )

    class FakeTavilyProvider:
        def search_news(self, keyword: str):
            return (tavily_item,)

    source = FutuMarketDataSource(
        quote_context=NewsContext(),
        futu_module=FakeSdk,
        news_fallback_provider=FakeTavilyProvider(),
    )

    assert source.search_news("半导体") == [tavily_item]


def test_nonempty_futu_news_is_supplemented_and_deduplicated_by_tavily() -> None:
    class FakeSdk:
        RET_OK = 0

    class NewsContext:
        def get_search_news(self, **kwargs):
            return 0, [
                {
                    "title": "Futu result",
                    "url": "https://example.test/shared",
                    "publish_time": "2026-08-11 09:00:00",
                }
            ]

    tavily_only = NewsItem(
        "Tavily-only result",
        "",
        "https://example.test/tavily-only",
        "",
        source="Tavily",
    )

    class FakeTavilyProvider:
        def search_news(self, keyword: str):
            return (
                NewsItem(
                    "Duplicate result",
                    "",
                    "https://example.test/shared",
                    "",
                    source="Tavily",
                ),
                tavily_only,
            )

    source = FutuMarketDataSource(
        quote_context=NewsContext(),
        futu_module=FakeSdk,
        news_fallback_provider=FakeTavilyProvider(),
        broader_news_coverage=True,
    )

    assert source.search_news("半导体") == [
        NewsItem(
            "Futu result",
            "",
            "https://example.test/shared",
            "2026-08-11 09:00:00",
        ),
        tavily_only,
    ]


def test_successful_futu_search_does_not_spend_tavily_quota_by_default() -> None:
    class FakeSdk:
        RET_OK = 0

    class NewsContext:
        def get_search_news(self, **kwargs):
            return 0, [
                {
                    "title": "Complete Futu result",
                    "url": "https://example.test/futu/complete",
                    "publish_time": "2026-08-11 09:00:00",
                }
            ]

    class CountingTavilyProvider:
        def __init__(self) -> None:
            self.call_count = 0

        def search_news(self, keyword: str):
            self.call_count += 1
            return ()

    fallback = CountingTavilyProvider()
    source = FutuMarketDataSource(
        quote_context=NewsContext(),
        futu_module=FakeSdk,
        news_fallback_provider=fallback,
    )

    assert len(source.search_news("半导体")) == 1
    assert fallback.call_count == 0


def test_tavily_provider_calls_the_official_search_endpoint_and_normalizes_results() -> None:
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "results": [
                        {
                            "title": "Semiconductor update",
                            "url": "https://example.test/tavily/1",
                            "content": "A concise search snippet",
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_open(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    provider = TavilyNewsProvider(
        "tvly-test-key",
        max_results=10,
        timeout_seconds=12,
        opener=fake_open,
    )

    assert provider.search_news("半导体") == [
        NewsItem(
            "Semiconductor update",
            "A concise search snippet",
            "https://example.test/tavily/1",
            "",
            source="Tavily",
        )
    ]
    request = captured["request"]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://api.tavily.com/search"
    assert request.get_header("Authorization") == "Bearer tvly-test-key"
    assert payload == {
        "query": "半导体",
        "search_depth": "basic",
        "max_results": 10,
        "topic": "news",
        "time_range": "day",
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    assert captured["timeout"] == 12
