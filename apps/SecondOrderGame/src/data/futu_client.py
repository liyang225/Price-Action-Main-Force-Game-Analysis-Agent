"""Futu OpenD adapter for the provider-neutral market data seam."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date
import math
import time
from typing import Any

from .models import Bar, CapitalFlow, DragonTiger, LimitPoolRecord, MarketSnapshot, NewsItem
from .protocol import DataSourceError
from .rate_limiter import RateLimiter


class FutuApiError(DataSourceError):
    """Futu returned a failed result or malformed payload."""


class FutuMarketDataSource:
    """Normalize Futu SDK payloads without exposing SDK types to callers."""

    def __init__(
        self,
        *,
        quote_context: Any | None = None,
        host: str = "127.0.0.1",
        port: int = 11111,
        futu_module: Any | None = None,
        rate_limiter: RateLimiter | None = None,
        retries: int = 0,
        retry_delay: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        dragon_tiger_provider: Any | None = None,
        news_fallback_provider: Any | None = None,
        broader_news_coverage: bool = False,
        breadth_provider: Any | None = None,
    ) -> None:
        if retries < 0:
            raise ValueError("retries must be non-negative")
        if retry_delay < 0:
            raise ValueError("retry_delay must be non-negative")
        if not isinstance(broader_news_coverage, bool):
            raise TypeError("broader_news_coverage must be a boolean")
        if broader_news_coverage and news_fallback_provider is None:
            raise ValueError(
                "broader_news_coverage requires a news fallback provider"
            )
        self._quote_context = quote_context
        self._owns_context = False
        self._host = host
        self._port = port
        self._futu_module = futu_module
        # Quotas belong to individual endpoints, not to an entire OpenD
        # connection.  Callers that batch a rate-limited endpoint (capital
        # flow and news prefetch) inject their own limiter; a default shared
        # limiter here would let background collection starve foreground
        # decision-material reads.
        self._rate_limiter = rate_limiter
        self._retries = retries
        self._retry_delay = retry_delay
        self._sleep = sleep
        self._dragon_tiger_provider = dragon_tiger_provider
        self._news_fallback_provider = news_fallback_provider
        self._broader_news_coverage = broader_news_coverage
        self._breadth_provider = breadth_provider

    def get_sector_constituents(self, sector_code: str) -> tuple[str, ...]:
        """Use Futu's plate-security mapping as the sole membership authority."""
        try:
            _, payload = self._invoke(
                "get_plate_stock", lambda: self._context().get_plate_stock(sector_code)
            )
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(
                "板块成分股查询失败"
                f"（quote_context={type(self._quote_context).__name__}）: {exc}"
            ) from exc
        rows = self._rows(payload)
        return tuple(dict.fromkeys(str(self._required(row, "code")) for row in rows))

    def get_market_snapshots(self, codes: Iterable[str]) -> tuple[MarketSnapshot, ...]:
        """Get constituent change rates in Futu's 400-code request batches."""
        normalized = tuple(
            code
            for code in dict.fromkeys(str(code).strip() for code in codes if str(code).strip())
            if _snapshot_supported(code)
        )
        snapshots: list[MarketSnapshot] = []
        for offset in range(0, len(normalized), 400):
            batch = normalized[offset : offset + 400]
            _, payload = self._invoke(
                "get_market_snapshot",
                lambda batch=batch: self._context().get_market_snapshot(list(batch)),
            )
            for row in self._rows(payload):
                raw_rate = row.get("change_rate")
                if raw_rate is None:
                    raw_rate = _change_rate_from_prices(row)
                if raw_rate is None:
                    continue
                try:
                    rate = float(raw_rate)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(rate):
                    continue
                snapshots.append(
                    MarketSnapshot(code=str(self._required(row, "code")), change_rate=rate)
                )
        return tuple(snapshots)

    def get_limit_pool(self, date: str) -> tuple[LimitPoolRecord, ...]:
        if self._breadth_provider is None:
            raise DataSourceError("limit-pool provider is not configured")
        try:
            return tuple(self._breadth_provider.get_limit_pool(date))
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(f"limit-pool provider failed: {exc}") from exc

    def get_trading_days(self, market: str, start: str, end: str) -> tuple[date, ...]:
        sdk = self._sdk()
        sdk_market = self._enum_member(sdk.TradeDateMarket, market, "trade-date market")
        _, payload = self._invoke(
            "request_trading_days",
            lambda: self._context().request_trading_days(
                market=sdk_market, start=start, end=end
            ),
        )
        result: list[date] = []
        for row in self._rows(payload):
            raw = row.get("time", row.get("trade_date", row.get("date")))
            if raw is None:
                raise FutuApiError("Futu trading-day payload is missing date")
            result.append(date.fromisoformat(str(raw)[:10]))
        return tuple(dict.fromkeys(result))

    def get_kline(
        self, code: str, ktype: str, start: str, end: str
    ) -> list[Bar]:
        sdk = self._sdk()
        sdk_ktype = self._enum_member(sdk.KLType, ktype, "K-line type")
        page_req_key = None
        seen_page_keys: set[object] = set()
        rows: list[Mapping[str, Any]] = []

        while True:
            ret, payload, next_page_key = self._invoke(
                "request_history_kline",
                lambda: self._context().request_history_kline(
                    code=code,
                    start=start,
                    end=end,
                    ktype=sdk_ktype,
                    max_count=1000,
                    page_req_key=page_req_key,
                ),
            )
            rows.extend(self._rows(payload))
            if next_page_key is None:
                break
            if next_page_key in seen_page_keys:
                raise FutuApiError("request_history_kline returned a repeated page key")
            seen_page_keys.add(next_page_key)
            page_req_key = next_page_key

        return [
            Bar(
                time_key=str(self._required(row, "time_key")),
                open=float(self._required(row, "open")),
                high=float(self._required(row, "high")),
                low=float(self._required(row, "low")),
                close=float(self._required(row, "close")),
                volume=int(self._required(row, "volume")),
                turnover=float(self._required(row, "turnover")),
            )
            for row in rows
        ]

    def get_capital_flow(self, code: str, date: str) -> CapitalFlow | None:
        sdk = self._sdk()
        ret, payload = self._invoke(
            "get_capital_flow",
            lambda: self._context().get_capital_flow(
                stock_code=code,
                period_type=sdk.PeriodType.DAY,
                start=date,
                end=date,
            ),
        )
        rows = self._rows(payload)
        if not rows:
            return None
        row = rows[-1]
        return CapitalFlow(
            date=date,
            code=code,
            super_in_flow=float(self._required(row, "super_in_flow")),
            big_in_flow=float(self._required(row, "big_in_flow")),
            mid_in_flow=float(self._required(row, "mid_in_flow")),
            sml_in_flow=float(self._required(row, "sml_in_flow")),
            main_in_flow=float(self._required(row, "main_in_flow")),
        )

    def get_capital_flow_range(
        self, code: str, start: str, end: str
    ) -> list[CapitalFlow]:
        """Fetch daily capital flows for ``code`` over a date range in one call.

        The Futu capital-flow endpoint is range-capable: a single request
        returns one row per trading day, which keeps the daily collection well
        inside the API rate quota.
        """
        sdk = self._sdk()
        _, payload = self._invoke(
            "get_capital_flow",
            lambda: self._context().get_capital_flow(
                stock_code=code,
                period_type=sdk.PeriodType.DAY,
                start=start,
                end=end,
            ),
        )
        flows: list[CapitalFlow] = []
        for row in self._rows(payload):
            day = str(
                row.get("capital_flow_item_time")
                or row.get("time")
                or row.get("date")
                or ""
            )[:10]
            if not day:
                continue
            try:
                flows.append(
                    CapitalFlow(
                        date=day,
                        code=code,
                        super_in_flow=float(self._required(row, "super_in_flow")),
                        big_in_flow=float(self._required(row, "big_in_flow")),
                        mid_in_flow=float(self._required(row, "mid_in_flow")),
                        sml_in_flow=float(self._required(row, "sml_in_flow")),
                        main_in_flow=float(self._required(row, "main_in_flow")),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return flows

    def get_sector_capital_flow_history(
        self, sector_name: str
    ) -> tuple[CapitalFlow, ...]:
        """Forward board flow to the AkShare provider attached to this source."""
        provider = self._dragon_tiger_provider
        method = getattr(provider, "get_sector_capital_flow_history", None)
        if not callable(method):
            raise DataSourceError("board capital-flow provider is not configured")
        try:
            return tuple(method(sector_name))
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(f"board capital-flow provider failed: {exc}") from exc

    def search_news(self, keyword: str) -> list[NewsItem]:
        primary_error: DataSourceError | None = None
        try:
            ret, payload = self._invoke(
                "get_search_news",
                lambda: self._context().get_search_news(keyword=keyword, max_count=100),
            )
            primary_items = self._normalize_news_rows(payload)
        except DataSourceError as exc:
            primary_error = exc
            primary_items = []

        if primary_items and not self._broader_news_coverage:
            return primary_items
        if self._news_fallback_provider is None:
            if primary_error is not None:
                raise primary_error
            return primary_items

        try:
            fallback_items = self._fallback_news(keyword)
        except DataSourceError as exc:
            if primary_items:
                return primary_items
            if primary_error is None:
                raise
            raise DataSourceError(
                f"Futu news search failed ({primary_error}); "
                f"Tavily fallback failed ({exc})"
            ) from exc
        return self._merge_news(primary_items, fallback_items)

    def _normalize_news_rows(self, payload: Any) -> list[NewsItem]:
        return [
            NewsItem(
                title=str(self._required(row, "title")),
                snippet=str(row.get("snippet", row.get("summary", ""))),
                url=str(self._required(row, "url")),
                published_date=str(row.get("publish_time", row.get("published_date", ""))),
                source=str(row.get("source", "")),
                related_securities=self._related_securities(row.get("related_securities", ())),
            )
            for row in self._rows(payload)
        ]

    def _fallback_news(self, keyword: str) -> list[NewsItem]:
        provider = self._news_fallback_provider
        try:
            method = getattr(provider, "search_news", None)
            if method is None:
                raise DataSourceError(
                    "Tavily fallback provider has no search_news method"
                )
            result = method(keyword)
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(f"Tavily fallback provider failed: {exc}") from exc

        if isinstance(result, str | bytes) or not isinstance(result, Iterable):
            raise DataSourceError(
                "Tavily fallback provider must return an iterable of NewsItem"
            )
        items = list(result)
        if not all(isinstance(item, NewsItem) for item in items):
            raise DataSourceError(
                "Tavily fallback provider must return only NewsItem values"
            )
        return items

    @staticmethod
    def _merge_news(
        primary_items: Iterable[NewsItem], fallback_items: Iterable[NewsItem]
    ) -> list[NewsItem]:
        merged: list[NewsItem] = []
        seen_urls: set[str] = set()
        for item in (*primary_items, *fallback_items):
            if item.url in seen_urls:
                continue
            merged.append(item)
            seen_urls.add(item.url)
        return merged

    def get_dragon_tiger(self, code: str, date: str) -> DragonTiger | None:
        provider = self._dragon_tiger_provider
        if provider is None:
            raise DataSourceError("dragon-tiger provider is not configured")
        try:
            if callable(provider):
                result = provider(code, date)
            else:
                method = getattr(provider, "get_dragon_tiger", None)
                if method is None:
                    raise DataSourceError("dragon-tiger provider has no get_dragon_tiger method")
                result = method(code, date)
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(f"dragon-tiger provider failed: {exc}") from exc
        if result is not None and not isinstance(result, DragonTiger):
            raise DataSourceError("dragon-tiger provider must return DragonTiger or None")
        return result

    def get_dragon_tiger_day(self, date: str) -> tuple[DragonTiger, ...]:
        """One market-wide 龙虎榜 list, forwarded to the configured provider."""
        provider = self._dragon_tiger_provider
        if provider is None:
            raise DataSourceError("dragon-tiger provider is not configured")
        try:
            if callable(provider):
                raise DataSourceError(
                    "dragon-tiger provider does not expose a daily-list method"
                )
            method = getattr(provider, "get_dragon_tiger_day", None)
            if method is None:
                raise DataSourceError(
                    "dragon-tiger provider has no get_dragon_tiger_day method"
                )
            return tuple(method(date))
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(f"dragon-tiger daily list failed: {exc}") from exc

    def close(self) -> None:
        if self._owns_context and self._quote_context is not None:
            self._quote_context.close()
            self._quote_context = None
            self._owns_context = False

    def __enter__(self) -> "FutuMarketDataSource":
        self._context()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _sdk(self) -> Any:
        if self._futu_module is None:
            try:
                import futu
            except ImportError as exc:
                raise DataSourceError("futu-api is not installed") from exc
            self._futu_module = futu
        return self._futu_module

    def _context(self) -> Any:
        if self._quote_context is None:
            sdk = self._sdk()
            try:
                self._quote_context = sdk.OpenQuoteContext(host=self._host, port=self._port)
            except Exception as exc:
                raise DataSourceError(f"failed to connect to Futu OpenD: {exc}") from exc
            self._owns_context = True
        return self._quote_context

    def _invoke(self, operation: str, call: Callable[[], Any]) -> Any:
        last_error: FutuApiError | None = None
        for attempt in range(self._retries + 1):
            if self._rate_limiter is not None:
                self._rate_limiter.require()
            try:
                result = call()
                if not isinstance(result, tuple) or len(result) < 2:
                    raise FutuApiError(f"{operation} returned a malformed result")
                if result[0] != self._sdk().RET_OK:
                    raise FutuApiError(f"{operation} failed: {result[1]}")
                return result
            except FutuApiError as exc:
                last_error = exc
            except Exception as exc:
                last_error = FutuApiError(f"{operation} failed: {exc}")
            if attempt < self._retries:
                self._sleep(self._retry_delay)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _rows(payload: Any) -> list[Mapping[str, Any]]:
        if payload is None:
            return []
        if isinstance(payload, Mapping):
            return [payload]
        to_dict = getattr(payload, "to_dict", None)
        if callable(to_dict):
            records = to_dict("records")
            if isinstance(records, list):
                return records
        if isinstance(payload, Iterable) and not isinstance(payload, (str, bytes)):
            rows = list(payload)
            if all(isinstance(row, Mapping) for row in rows):
                return rows
        raise FutuApiError(f"unexpected Futu payload type: {type(payload).__name__}")

    @staticmethod
    def _required(row: Mapping[str, Any], field: str) -> Any:
        if field not in row:
            raise FutuApiError(f"Futu payload is missing required field: {field}")
        return row[field]

    @staticmethod
    def _enum_member(enum_type: Any, value: Any, label: str) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return getattr(enum_type, value)
        except AttributeError as exc:
            raise DataSourceError(f"unsupported {label}: {value}") from exc

    @staticmethod
    def _related_securities(value: Any) -> tuple[str, ...]:
        if not value:
            return ()
        if isinstance(value, str):
            return (value,)
        return tuple(
            str(item.get("code", item)) if isinstance(item, Mapping) else str(item)
            for item in value
        )


def _snapshot_supported(code: str) -> bool:
    """Skip BSE constituents that this OpenD snapshot endpoint rejects.

    Some Futu plate lists expose Beijing Exchange securities under an ``SH.92``
    code, but the installed OpenD responds ``unknown stock`` for them and
    rejects the entire batch.  They cannot contribute usable capital-flow
    data through this provider, so omitting them preserves the remaining
    A-share component ranking.
    """
    return not code.startswith(("SH.92", "SZ.92"))


def _change_rate_from_prices(row: Mapping[str, Any]) -> float | None:
    """Support older OpenD snapshots that omit the ``change_rate`` column."""
    try:
        last_price = float(row["last_price"])
        previous_close = float(row["prev_close_price"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(last_price) or not math.isfinite(previous_close) or previous_close == 0:
        return None
    return (last_price - previous_close) / previous_close * 100.0


FutuClient = FutuMarketDataSource
