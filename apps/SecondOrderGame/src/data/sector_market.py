"""Sector-index price inputs and exchange-session resolution."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
import math
from typing import Any

from src.data.models import Bar


@dataclass(frozen=True, slots=True)
class SectorPriceAction:
    status: str
    sector_code: str
    trading_date: date | None
    daily_return: float | None
    two_day_return: float | None
    bar_dates: tuple[str, ...]
    error: str | None = None

    def calculator_input(self) -> dict[str, float]:
        result: dict[str, float] = {}
        if self.daily_return is not None:
            result["daily_return"] = self.daily_return
        if self.two_day_return is not None:
            result["two_day_return"] = self.two_day_return
        return result


@dataclass(frozen=True, slots=True)
class TradingSession:
    trading_date: date | None
    is_trading_day: bool
    source: str


def load_sector_price_action(
    source: Any,
    sector_code: str,
    *,
    start: str,
    end: str,
) -> SectorPriceAction:
    try:
        bars = tuple(source.get_kline(sector_code, "K_DAY", start, end))
        ordered = _daily_closes(bars, date.fromisoformat(end))
    except Exception as exc:
        return SectorPriceAction("unavailable", sector_code, None, None, None, (), str(exc) or type(exc).__name__)
    if not ordered:
        return SectorPriceAction("insufficient_data", sector_code, None, None, None, (), "板块指数无日线数据")
    closes = [item[1] for item in ordered]
    daily = closes[-1] / closes[-2] - 1.0 if len(closes) >= 2 else None
    two_day = closes[-1] / closes[-3] - 1.0 if len(closes) >= 3 else None
    return SectorPriceAction(
        "ready" if daily is not None else "insufficient_data",
        sector_code,
        ordered[-1][0],
        daily,
        two_day,
        tuple(item[0].isoformat() for item in ordered[-3:]),
        None if daily is not None else "板块指数至少需要两个交易日收盘价",
    )


def resolve_trading_session(
    source: Any, current_date: date, price_action: SectorPriceAction
) -> TradingSession:
    getter = getattr(source, "get_trading_days", None)
    if callable(getter):
        start = current_date.replace(day=1).isoformat()
        try:
            days = tuple(_as_date(value) for value in getter(_market(current_date, price_action.sector_code), start, current_date.isoformat()))
        except Exception:
            days = ()
        valid = tuple(sorted(value for value in days if value <= current_date))
        if valid:
            latest = valid[-1]
            return TradingSession(latest, latest == current_date, "exchange_calendar")
    if price_action.trading_date is not None:
        return TradingSession(
            price_action.trading_date,
            price_action.trading_date == current_date,
            "sector_index_bar",
        )
    return TradingSession(None, False, "unavailable")


def _daily_closes(bars: Sequence[Bar], as_of: date) -> tuple[tuple[date, float], ...]:
    closes: dict[date, float] = {}
    for bar in bars:
        try:
            bar_date = date.fromisoformat(str(bar.time_key)[:10])
            close = float(bar.close)
        except (TypeError, ValueError):
            continue
        if bar_date <= as_of and math.isfinite(close) and close > 0:
            closes[bar_date] = close
    return tuple(sorted(closes.items()))


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _market(_current_date: date, sector_code: str) -> str:
    prefix = sector_code.split(".", 1)[0].upper()
    return "CN" if prefix in {"SH", "SZ"} else prefix if prefix in {"HK", "US"} else "CN"


__all__ = ["SectorPriceAction", "TradingSession", "load_sector_price_action", "resolve_trading_session"]
