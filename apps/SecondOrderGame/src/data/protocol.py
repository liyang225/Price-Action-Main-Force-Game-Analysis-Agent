"""The single business-facing boundary for external market data."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol, runtime_checkable

from .models import Bar, CapitalFlow, DragonTiger, LimitPoolRecord, MarketSnapshot, NewsItem


class DataSourceError(RuntimeError):
    """An external source failed or is not configured."""


@runtime_checkable
class MarketDataSource(Protocol):
    def get_kline(
        self, code: str, ktype: str, start: str, end: str
    ) -> Sequence[Bar]: ...

    def get_capital_flow(self, code: str, date: str) -> CapitalFlow | None: ...

    def get_capital_flow_range(
        self, code: str, start: str, end: str
    ) -> Sequence[CapitalFlow]: ...

    def get_sector_capital_flow_history(
        self, sector_name: str
    ) -> Sequence[CapitalFlow]: ...

    def search_news(self, keyword: str) -> Sequence[NewsItem]: ...

    def get_dragon_tiger(self, code: str, date: str) -> DragonTiger | None: ...

    def get_dragon_tiger_day(self, date: str) -> Sequence[DragonTiger]: ...

    def get_sector_constituents(self, sector_code: str) -> Sequence[str]: ...

    def get_market_snapshots(self, codes: Sequence[str]) -> Sequence[MarketSnapshot]: ...

    def get_limit_pool(self, date: str) -> Sequence[LimitPoolRecord]: ...

    def get_trading_days(self, market: str, start: str, end: str) -> Sequence[date]: ...
