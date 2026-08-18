"""Adapt PA's live Kline source to SecondOrderGame's market-data protocol."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from collections.abc import Mapping, Sequence
import re
from typing import Any
from zoneinfo import ZoneInfo

from src.data.models import Bar, NewsItem


CN_TZ = ZoneInfo("Asia/Shanghai")
MAX_NEWS_ITEMS = 18
MIN_NEWS_ITEMS = 5
MAX_NEWS_ITEMS_LIMIT = 30


class PAMarketDataAdapter:
    """Read PA snapshots without owning or disconnecting PA's data source."""

    def __init__(
        self,
        pa_source: Any,
        *,
        max_bars: int = 1000,
        news_fallback_provider: Any | None = None,
        sector_market_source: Any | None = None,
        max_news_items: int = MAX_NEWS_ITEMS,
    ) -> None:
        if not callable(getattr(pa_source, "latest_snapshot", None)):
            raise TypeError("PA data source must expose latest_snapshot(n)")
        if not isinstance(max_news_items, int) or isinstance(max_news_items, bool):
            raise TypeError("max_news_items must be an integer")
        if not MIN_NEWS_ITEMS <= max_news_items <= MAX_NEWS_ITEMS_LIMIT:
            raise ValueError(
                f"max_news_items must be between {MIN_NEWS_ITEMS} and "
                f"{MAX_NEWS_ITEMS_LIMIT}"
            )
        self._source = pa_source
        self._max_bars = max_bars
        self._news_fallback_provider = news_fallback_provider
        self._sector_market_source = sector_market_source
        self._max_news_items = max_news_items

    def get_kline(self, code: str, ktype: str, start: str, end: str) -> list[Bar]:
        if ktype == "K_120M":
            subscribed_symbol = str(getattr(self._source, "_symbol", "") or "").upper()
            if subscribed_symbol and subscribed_symbol == code.upper():
                return self._subscribed_bars()
            # 板块指数（或非订阅 symbol）的 K_120M：走板块行情数据源
            getter = getattr(self._sector_market_source, "get_kline", None)
            if not callable(getter):
                raise ValueError("板块 K_120M 需要配置统一板块行情数据源")
            return list(getter(code, ktype, start, end))
        getter = getattr(self._sector_market_source, "get_kline", None)
        if not callable(getter):
            raise ValueError("板块日线需要配置统一板块行情数据源")
        return list(getter(code, ktype, start, end))

    def _subscribed_bars(self) -> list[Bar]:
        """Read the PA-subscribed symbol's closed K_120M bars from its snapshot."""
        subscribed_timeframe = str(getattr(self._source, "_timeframe", "") or "")
        if subscribed_timeframe and subscribed_timeframe not in {"2h", "120m"}:
            raise ValueError("请先在 PA 获取 120 分钟周期数据")
        pa_bars = self._source.latest_snapshot(self._max_bars)
        result: list[Bar] = []
        for item in reversed(pa_bars):
            if not bool(getattr(item, "closed", True)):
                continue
            timestamp = datetime.fromtimestamp(
                float(item.ts_open) / 1000,
                tz=CN_TZ,
            )
            result.append(
                Bar(
                    timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    float(item.open),
                    float(item.high),
                    float(item.low),
                    float(item.close),
                    float(item.volume),
                    float(getattr(item, "amount", 0.0) or 0.0),
                )
            )
        return result

    def get_capital_flow(self, code: str, date: str) -> Any:
        getter = getattr(self._sector_market_source, "get_capital_flow", None)
        if callable(getter):
            return getter(code, date)
        getter = getattr(self._source, "get_capital_flow", None)
        if not callable(getter):
            raise NotImplementedError("PA 当前数据源不提供资金流")
        return getter(code, date)

    def get_capital_flow_range(self, code: str, start: str, end: str) -> Any:
        getter = getattr(self._sector_market_source, "get_capital_flow_range", None)
        if callable(getter):
            return getter(code, start, end)
        getter = getattr(self._source, "get_capital_flow_range", None)
        if not callable(getter):
            raise NotImplementedError("PA 当前数据源不提供资金流范围查询")
        return getter(code, start, end)

    def get_sector_capital_flow_history(self, sector_name: str) -> Any:
        getter = getattr(self._sector_market_source, "get_sector_capital_flow_history", None)
        if not callable(getter):
            raise NotImplementedError("板块行情数据源不提供板块资金流")
        return getter(sector_name)

    def search_news(self, keyword: str) -> Any:
        """Merge Futu headlines (title/link/related securities) with Tavily text.

        Futu's ``get_search_news`` returns only titles and links; Tavily returns
        summaries.  Merge both so the cached ``NewsItem`` carries a snippet for
        the lexical sentiment score and the LLM subject-purpose preanalysis.
        """
        return _merge_news(
            self._futu_news(keyword),
            self._tavily_news(keyword),
            max_items=self._max_news_items,
        )

    def _futu_news(self, keyword: str) -> list[NewsItem]:
        getter = getattr(self._source, "search_news", None)
        if not callable(getter):
            return []
        try:
            return _normalize_news(getter(keyword))
        except Exception:
            return []

    def _tavily_news(self, keyword: str) -> list[NewsItem]:
        getter = getattr(self._news_fallback_provider, "search_news", None)
        if not callable(getter):
            return []
        try:
            return _normalize_news(getter(keyword))
        except Exception:
            return []

    def get_dragon_tiger(self, code: str, date: str) -> Any:
        # 龙虎榜由板块行情数据源（FutuMarketDataSource，内挂 AkShare provider）
        # 提供；PA 的个股订阅源不含龙虎榜。
        sector_getter = getattr(self._sector_market_source, "get_dragon_tiger", None)
        if callable(sector_getter):
            return sector_getter(code, date)
        getter = getattr(self._source, "get_dragon_tiger", None)
        if not callable(getter):
            raise NotImplementedError("PA 当前数据源不提供龙虎榜")
        return getter(code, date)

    def get_dragon_tiger_day(self, date: str) -> Any:
        # 全市场当日龙虎榜列表（不含逐股席位增强），供板块分析按成分股过滤。
        getter = getattr(self._sector_market_source, "get_dragon_tiger_day", None)
        if not callable(getter):
            raise NotImplementedError("PA 当前数据源不提供全市场龙虎榜列表")
        return getter(date)

    def get_limit_pool(self, date: str) -> Any:
        # 连板池（涨停/跌停）由板块行情数据源（FutuMarketDataSource，内挂
        # AkShare breadth provider）提供。
        getter = getattr(self._sector_market_source, "get_limit_pool", None)
        if not callable(getter):
            raise NotImplementedError("PA 当前数据源不提供连板池")
        return getter(date)

    def get_sector_constituents(self, sector_code: str) -> Any:
        # 板块成分股由板块行情数据源（FutuMarketDataSource 的
        # get_plate_stock）提供；PA 的个股订阅源不含板块成分关系。
        getter = getattr(self._sector_market_source, "get_sector_constituents", None)
        if not callable(getter):
            raise NotImplementedError(
                "板块行情数据源未配置或未提供 get_sector_constituents（板块成分股查询）；"
                "请检查 PAMarketDataAdapter 构造时是否注入了 sector_market_source"
            )
        return getter(sector_code)

    def get_market_snapshots(self, codes: Sequence[str]) -> Any:
        getter = getattr(self._sector_market_source, "get_market_snapshots", None)
        if not callable(getter):
            raise NotImplementedError("板块行情数据源不提供个股快照")
        return getter(codes)

    def get_trading_days(self, market: str, start: str, end: str) -> Any:
        getter = getattr(self._sector_market_source, "get_trading_days", None)
        if not callable(getter):
            raise NotImplementedError("板块行情数据源未提供交易日历")
        return getter(market, start, end)

    def validate_sector_code(self, sector_code: str) -> None:
        """Validate plate-code validity against the configured providers.

        The sector market source is authoritative; fall back to the PA
        subscribed source only when no sector source is wired.  A missing
        source is a hard configuration error — never silently skip validation,
        otherwise the production chain advances with an invalid sector code
        until a downstream K-line / sentiment step fails obscurely.
        """
        getter = getattr(self._sector_market_source, "get_sector_constituents", None)
        if not callable(getter):
            getter = getattr(self._source, "get_sector_constituents", None)
        if not callable(getter):
            raise ValueError(
                "板块行情数据源未配置，无法校验板块代码有效性；"
                "请检查 PAMarketDataAdapter 构造时是否注入了 sector_market_source"
            )
        constituents = tuple(getter(sector_code))
        if not constituents:
            raise ValueError(f"富途没有返回板块成分股: {sector_code}")

    def close(self) -> None:
        closer = getattr(self._sector_market_source, "close", None)
        if callable(closer):
            closer()


__all__ = ["PAMarketDataAdapter"]


def _merge_news(
    futu_items: Sequence[NewsItem],
    tavily_items: Sequence[NewsItem],
    *,
    max_items: int = MAX_NEWS_ITEMS,
) -> list[NewsItem]:
    """Backfill Futu headlines with Tavily text, then dedupe and cap the batch.

    Futu supplies authoritative titles and related securities but no body;
    Tavily supplies bodies.  A body is attached to a Futu item only when the
    normalized titles match; unmatched Tavily items are kept as their own
    records.  Records with a snippet are prioritized before the ``max_items``
    cut so the sentiment score has text to work on.
    """
    unmatched_tavily = list(tavily_items)
    enriched: list[NewsItem] = []
    for futu_item in futu_items:
        target = futu_item
        if not futu_item.snippet:
            for index, tavily_item in enumerate(unmatched_tavily):
                if tavily_item.snippet and _titles_match(futu_item.title, tavily_item.title):
                    target = replace(futu_item, snippet=tavily_item.snippet)
                    unmatched_tavily.pop(index)
                    break
        enriched.append(target)

    deduped = _dedupe_news([*enriched, *unmatched_tavily])
    with_text = [item for item in deduped if item.snippet]
    without_text = [item for item in deduped if not item.snippet]
    return (with_text + without_text)[:max_items]


def _dedupe_news(items: Sequence[NewsItem]) -> list[NewsItem]:
    seen: set[tuple[str, str]] = set()
    result: list[NewsItem] = []
    for item in items:
        key = (item.url or "", _normalize_title(item.title))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _titles_match(left: str, right: str) -> bool:
    left_norm = _normalize_title(left)
    if not left_norm:
        return False
    return left_norm == _normalize_title(right)


def _normalize_title(title: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (title or "").casefold())


def _normalize_news(value: Any) -> list[NewsItem]:
    """Normalize PA/Futu summary rows without following article URLs."""
    if value is None:
        return []
    if hasattr(value, "to_dict") and not isinstance(value, Mapping):
        try:
            value = value.to_dict("records")
        except TypeError:
            pass
    if isinstance(value, NewsItem):
        return [value]
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    items: list[NewsItem] = []
    for row in value:
        if isinstance(row, NewsItem):
            items.append(row)
            continue
        if not isinstance(row, Mapping):
            continue
        title = str(row.get("title") or row.get("headline") or "").strip()
        snippet = str(
            row.get("snippet") or row.get("summary") or row.get("content") or ""
        ).strip()
        if not title and not snippet:
            continue
        related = row.get("related_securities") or row.get("codes") or ()
        if isinstance(related, str):
            related = (related,)
        items.append(
            NewsItem(
                title=title or snippet[:80],
                snippet=snippet,
                url=str(row.get("url") or row.get("link") or ""),
                published_date=str(
                    row.get("published_date")
                    or row.get("publish_time")
                    or row.get("time")
                    or ""
                ),
                source=str(row.get("source") or "Futu OpenD"),
                related_securities=tuple(str(item) for item in related),
            )
        )
    return items
