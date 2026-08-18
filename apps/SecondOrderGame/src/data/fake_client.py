"""In-memory implementation of the market data seam."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from .models import Bar, CapitalFlow, DragonTiger, LimitPoolRecord, MarketSnapshot, NewsItem
from .protocol import DataSourceError


class FakeMarketDataSource:
    """Return configured data, empty results, or explicit failures offline."""

    def __init__(
        self,
        kline_data: Mapping[tuple[str, str, str, str], Sequence[Bar]] | None = None,
        capital_flow_data: Mapping[tuple[str, str], CapitalFlow | None] | None = None,
        sector_capital_flow_data: Mapping[str, Sequence[CapitalFlow]] | None = None,
        news_data: Mapping[str, Sequence[NewsItem]] | None = None,
        fallback_news_data: Mapping[str, Sequence[NewsItem]] | None = None,
        dragon_tiger_data: Mapping[
            tuple[str, str], DragonTiger | Sequence[DragonTiger] | None
        ]
        | None = None,
        failures: Mapping[object, BaseException | str] | None = None,
        sector_constituents: Mapping[str, Sequence[str]] | None = None,
        market_snapshots: Mapping[str, MarketSnapshot | float] | None = None,
        limit_pool_data: Mapping[str, Sequence[LimitPoolRecord]] | None = None,
        trading_days: Sequence[date | str] | None = None,
    ) -> None:
        self._kline = dict(kline_data or {})
        self._capital = dict(capital_flow_data or {})
        self._sector_capital = {
            str(name): tuple(values)
            for name, values in (sector_capital_flow_data or {}).items()
        }
        self._news = dict(news_data or {})
        self._fallback_news = dict(fallback_news_data or {})
        self._dragon = dict(dragon_tiger_data or {})
        self._failures = dict(failures or {})
        self._sector_constituents = dict(sector_constituents or {})
        self._market_snapshots = {
            str(code): (
                value if isinstance(value, MarketSnapshot) else MarketSnapshot(str(code), float(value))
            )
            for code, value in (market_snapshots or {}).items()
        }
        self._limit_pool = dict(limit_pool_data or {})
        self._trading_days = None if trading_days is None else tuple(
            value if isinstance(value, date) else date.fromisoformat(value)
            for value in trading_days
        )

    def get_kline(
        self, code: str, ktype: str, start: str, end: str
    ) -> list[Bar]:
        key = (code, ktype, start, end)
        self._raise_configured_failure("get_kline", key)
        return list(self._kline.get(key, ()))

    def get_capital_flow(self, code: str, date: str) -> CapitalFlow | None:
        key = (code, date)
        self._raise_configured_failure("get_capital_flow", key)
        return self._capital.get(key)

    def get_capital_flow_range(
        self, code: str, start: str, end: str
    ) -> tuple[CapitalFlow, ...]:
        key = (code, start, end)
        self._raise_configured_failure("get_capital_flow_range", key)
        lower, upper = date.fromisoformat(start), date.fromisoformat(end)
        values = [
            flow
            for (flow_code, flow_date), flow in self._capital.items()
            if flow_code == code and lower <= date.fromisoformat(flow_date) <= upper
        ]
        return tuple(sorted(values, key=lambda item: item.date))

    def get_sector_capital_flow_history(self, sector_name: str) -> tuple[CapitalFlow, ...]:
        self._raise_configured_failure("get_sector_capital_flow_history", sector_name)
        return self._sector_capital.get(sector_name, ())

    def search_news(self, keyword: str) -> list[NewsItem]:
        try:
            self._raise_configured_failure("search_news", keyword)
        except DataSourceError:
            if keyword not in self._fallback_news:
                raise
            return list(self._fallback_news[keyword])

        primary_result = list(self._news.get(keyword, ()))
        if primary_result or keyword not in self._fallback_news:
            return primary_result
        return list(self._fallback_news[keyword])

    def get_dragon_tiger(self, code: str, date: str) -> DragonTiger | None:
        key = (code, date)
        self._raise_configured_failure("get_dragon_tiger", key)
        value = self._dragon.get(key)
        if value is None or isinstance(value, DragonTiger):
            return value
        records = tuple(value)
        if not records:
            return None
        if not all(isinstance(record, DragonTiger) for record in records):
            raise DataSourceError("dragon-tiger fixtures must contain DragonTiger values")
        return _merge_dragon_tiger_records(records)

    def get_dragon_tiger_records(
        self, code: str, date: str
    ) -> tuple[DragonTiger, ...]:
        key = (code, date)
        self._raise_configured_failure("get_dragon_tiger", key)
        value = self._dragon.get(key)
        if value is None:
            return ()
        if isinstance(value, DragonTiger):
            return (value,)
        records = tuple(value)
        if not all(isinstance(record, DragonTiger) for record in records):
            raise DataSourceError("dragon-tiger fixtures must contain DragonTiger values")
        return records

    def get_dragon_tiger_day(self, date: str) -> tuple[DragonTiger, ...]:
        """All dragon-tiger records for a date, regardless of code."""
        self._raise_configured_failure("get_dragon_tiger_day", date)
        result: list[DragonTiger] = []
        for (code, record_date), value in self._dragon.items():
            if record_date != date:
                continue
            if isinstance(value, DragonTiger):
                result.append(value)
            else:
                result.extend(record for record in tuple(value))
        return tuple(result)

    def get_sector_constituents(self, sector_code: str) -> tuple[str, ...]:
        self._raise_configured_failure("get_sector_constituents", sector_code)
        return tuple(self._sector_constituents.get(sector_code, ()))

    def get_market_snapshots(self, codes: Sequence[str]) -> tuple[MarketSnapshot, ...]:
        normalized = tuple(str(code) for code in codes)
        self._raise_configured_failure("get_market_snapshots", normalized)
        return tuple(
            self._market_snapshots[code]
            for code in normalized
            if code in self._market_snapshots
        )

    def get_limit_pool(self, date: str) -> tuple[LimitPoolRecord, ...]:
        self._raise_configured_failure("get_limit_pool", date)
        return tuple(self._limit_pool.get(date, ()))

    def get_trading_days(self, market: str, start: str, end: str) -> tuple[date, ...]:
        key = (market, start, end)
        self._raise_configured_failure("get_trading_days", key)
        lower, upper = date.fromisoformat(start), date.fromisoformat(end)
        if self._trading_days is not None:
            return tuple(value for value in self._trading_days if lower <= value <= upper)
        values: list[date] = []
        current = lower
        while current <= upper:
            if current.weekday() < 5:
                values.append(current)
            current += timedelta(days=1)
        return tuple(values)

    def _raise_configured_failure(self, operation: str, key: Any) -> None:
        failure = self._failures.get((operation, key), self._failures.get(operation))
        if failure is None:
            return
        if isinstance(failure, DataSourceError):
            raise failure
        if isinstance(failure, BaseException):
            raise DataSourceError(str(failure)) from failure
        raise DataSourceError(str(failure))


def _merge_dragon_tiger_records(records: tuple[DragonTiger, ...]) -> DragonTiger:
    if len(records) == 1:
        return records[0]

    def total(field: str) -> float | None:
        values = [getattr(record, field) for record in records]
        return (
            None
            if all(value is None for value in values)
            else float(sum(value or 0.0 for value in values))
        )

    return DragonTiger(
        date=records[0].date,
        code=records[0].code,
        reason="；".join(dict.fromkeys(record.reason for record in records if record.reason)),
        net_buy_amount=sum(record.net_buy_amount for record in records),
        buy_amount=total("buy_amount"),
        sell_amount=total("sell_amount"),
        institution_net_buy=total("institution_net_buy"),
        institution_net_sell=total("institution_net_sell"),
        hot_money_net_buy=total("hot_money_net_buy"),
        hot_money_net_sell=total("hot_money_net_sell"),
        institution_seats=tuple(
            dict.fromkeys(seat for record in records for seat in record.institution_seats)
        ),
        hot_money_seats=tuple(
            dict.fromkeys(seat for record in records for seat in record.hot_money_seats)
        ),
        buy_seats=tuple(dict.fromkeys(seat for record in records for seat in record.buy_seats)),
        sell_seats=tuple(
            dict.fromkeys(seat for record in records for seat in record.sell_seats)
        ),
        source=records[0].source,
        source_reference=";".join(
            dict.fromkeys(
                record.source_reference for record in records if record.source_reference
            )
        ),
    )
