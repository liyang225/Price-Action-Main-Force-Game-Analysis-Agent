"""Rate-limited background filling of the daily news-material cache."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from threading import Event, Lock, Thread
import time

from .daily_cache import DailyMaterialCache
from .models import NewsItem
from .protocol import DataSourceError, MarketDataSource
from .rate_limiter import RateLimiter


NEWS_CACHE_CATEGORY = "news"


@dataclass(frozen=True, slots=True)
class NewsPrefetchFailure:
    """One sector that could not be refreshed during a round."""

    sector: str
    reason: str


@dataclass(frozen=True, slots=True)
class NewsPrefetchReport:
    """Observable result of one frozen-sector prefetch round."""

    attempted_sectors: tuple[str, ...]
    cached_sectors: tuple[str, ...]
    failures: tuple[NewsPrefetchFailure, ...]
    elapsed_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {
            "attempted_sectors": list(self.attempted_sectors),
            "cached_sectors": list(self.cached_sectors),
            "failures": [
                {"sector": failure.sector, "reason": failure.reason}
                for failure in self.failures
            ],
            "elapsed_seconds": self.elapsed_seconds,
        }


class NewsPrefetchTask:
    """Continuously prefetch frozen sector rounds on an owned worker thread."""

    def __init__(
        self,
        source: MarketDataSource,
        cache: DailyMaterialCache,
        *,
        rate_limiter: RateLimiter | None = None,
        interval_seconds: float = 3.0,
        round_interval_seconds: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if interval_seconds < 0:
            raise ValueError("interval_seconds must be non-negative")
        if round_interval_seconds < 0:
            raise ValueError("round_interval_seconds must be non-negative")
        self._source = source
        self._cache = cache
        self._clock = clock
        self._sleep = sleep
        self._interval_seconds = float(interval_seconds)
        self._round_interval_seconds = float(round_interval_seconds)
        self._rate_limiter = rate_limiter or RateLimiter(clock=clock)
        self._stop_event = Event()
        self._lifecycle_lock = Lock()
        self._thread: Thread | None = None
        self._background_error: Exception | None = None
        self._last_report: NewsPrefetchReport | None = None
        self._round_count = 0

    @property
    def is_running(self) -> bool:
        with self._lifecycle_lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def background_error(self) -> Exception | None:
        with self._lifecycle_lock:
            return self._background_error

    @property
    def last_report(self) -> NewsPrefetchReport | None:
        with self._lifecycle_lock:
            return self._last_report

    def status(self) -> dict[str, object]:
        """Return a UI-safe view of the worker and its most recent round."""
        with self._lifecycle_lock:
            running = self._thread is not None and self._thread.is_alive()
            error = self._background_error
            report = self._last_report
            return {
                "state": "running" if running else "error" if error else "stopped",
                "round_count": self._round_count,
                "last_round": report.to_dict() if report is not None else None,
                "error": str(error) if error is not None else None,
            }

    def start(
        self,
        sectors: Iterable[str] | Callable[[], Iterable[str]],
    ) -> None:
        """Start recurring prefetch rounds without blocking the caller."""

        if callable(sectors):
            supplier = sectors
        else:
            frozen_sectors = tuple(sectors)
            supplier = lambda: frozen_sectors

        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("news prefetch task is already running")
            self._stop_event.clear()
            self._background_error = None
            thread = Thread(
                target=self._run_background,
                args=(supplier,),
                name="second-order-game-news-prefetch",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self, timeout: float | None = None) -> None:
        """Request shutdown and wait for the worker to exit."""

        with self._lifecycle_lock:
            thread = self._thread
            self._stop_event.set()
        if thread is None:
            return
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError("news prefetch task did not stop before the timeout")
        with self._lifecycle_lock:
            if self._thread is thread:
                self._thread = None

    def _run_background(self, supplier: Callable[[], Iterable[str]]) -> None:
        try:
            while not self._stop_event.is_set():
                report = self._run_round(supplier(), stop_event=self._stop_event)
                self._record_report(report)
                if self._stop_event.wait(self._round_interval_seconds):
                    return
        except Exception as exc:
            with self._lifecycle_lock:
                self._background_error = exc

    def run_round(self, sectors: Iterable[str]) -> NewsPrefetchReport:
        """Search every sector once, cache successes, and isolate source failures."""
        report = self._run_round(sectors)
        self._record_report(report)
        return report

    def _record_report(self, report: NewsPrefetchReport) -> None:
        with self._lifecycle_lock:
            self._last_report = report
            self._round_count += 1

    def _run_round(
        self, sectors: Iterable[str], *, stop_event: Event | None = None
    ) -> NewsPrefetchReport:

        attempted = _normalize_sectors(sectors)
        started_at = self._now()
        cached: list[str] = []
        failures: list[NewsPrefetchFailure] = []

        for index, sector in enumerate(attempted):
            if stop_event is not None and stop_event.is_set():
                break
            if not self._wait_for_rate_limit(stop_event):
                break
            call_started_at = self._now()
            try:
                news_items = _validated_news_items(self._source.search_news(sector))
            except DataSourceError as exc:
                failures.append(NewsPrefetchFailure(sector=sector, reason=str(exc)))
            else:
                self._cache.put(NEWS_CACHE_CATEGORY, sector, news_items)
                cached.append(sector)

            if index + 1 < len(attempted):
                call_elapsed = self._now() - call_started_at
                if self._pause(
                    max(0.0, self._interval_seconds - call_elapsed), stop_event
                ):
                    break

        return NewsPrefetchReport(
            attempted_sectors=attempted,
            cached_sectors=tuple(cached),
            failures=tuple(failures),
            elapsed_seconds=self._now() - started_at,
        )

    def _wait_for_rate_limit(self, stop_event: Event | None) -> bool:
        while not self._rate_limiter.try_acquire():
            retry_after = self._rate_limiter.retry_after()
            if retry_after <= 0:
                continue
            if self._pause(retry_after, stop_event):
                return False
        return True

    def _pause(self, seconds: float, stop_event: Event | None) -> bool:
        if stop_event is not None:
            return stop_event.wait(seconds)
        self._sleep(seconds)
        return False

    def _now(self) -> float:
        now = self._clock()
        if not isinstance(now, int | float):
            raise TypeError("clock must return a number")
        return float(now)


def _normalize_sectors(sectors: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for sector in sectors:
        if not isinstance(sector, str) or not sector.strip():
            raise ValueError("sectors must contain non-empty strings")
        name = sector.strip()
        if name not in seen:
            normalized.append(name)
            seen.add(name)
    return tuple(normalized)


def _validated_news_items(items: Sequence[NewsItem]) -> tuple[NewsItem, ...]:
    try:
        result = tuple(items)
    except TypeError as exc:
        raise DataSourceError("search_news must return a sequence of NewsItem") from exc
    if not all(isinstance(item, NewsItem) for item in result):
        raise DataSourceError("search_news must return only NewsItem values")
    return result
