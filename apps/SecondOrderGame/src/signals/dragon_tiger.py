"""Auditable institution and hot-money features from 龙虎榜 records."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as calendar_date
from enum import Enum
import re

from src.data.daily_cache import DailyMaterialCache
from src.data.models import DragonTiger
from src.data.protocol import DataSourceError, MarketDataSource


DRAGON_TIGER_CACHE_CATEGORY = "dragon_tiger"


class SignalStatus(str, Enum):
    OK = "ok"
    NO_DATA = "no_data"
    MISSING_FIELDS = "missing_fields"
    CONFLICT = "conflict"
    SOURCE_ERROR = "source_error"


@dataclass(frozen=True, slots=True)
class DragonTigerSignal:
    """Stable, cache-safe feature set with its source and quality state."""

    date: str
    code: str
    status: SignalStatus
    institution_net_buy: float | None = None
    institution_net_sell: float | None = None
    hot_money_net_buy: float | None = None
    hot_money_net_sell: float | None = None
    institution_seats: tuple[str, ...] = ()
    hot_money_seats: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    source: str = ""
    source_reference: str = ""
    notes: tuple[str, ...] = ()

    @property
    def institution_net_buy_amount(self) -> float | None:
        return self.institution_net_buy

    @property
    def institution_net_sell_amount(self) -> float | None:
        return self.institution_net_sell

    @property
    def hot_money_net_buy_amount(self) -> float | None:
        return self.hot_money_net_buy

    @property
    def hot_money_net_sell_amount(self) -> float | None:
        return self.hot_money_net_sell


# Descriptive alias for callers that treat the output as one extraction result.
DragonTigerSignalResult = DragonTigerSignal


class DragonTigerSignalExtractor:
    """Read the market-data seam and place one result in the daily cache."""

    def __init__(self, source: MarketDataSource, cache: DailyMaterialCache) -> None:
        self._source = source
        self._cache = cache

    def extract(self, code: str, date: str) -> DragonTigerSignal:
        _validate_request(code, date)
        try:
            records_method = getattr(self._source, "get_dragon_tiger_records", None)
            if callable(records_method):
                records = tuple(records_method(code, date))
                record = _merge_records(records) if records else None
            else:
                record = self._source.get_dragon_tiger(code, date)
        except (DataSourceError, TypeError, ValueError, AttributeError) as exc:
            result = DragonTigerSignal(
                date=date,
                code=code,
                status=SignalStatus.SOURCE_ERROR,
                notes=(str(exc),),
            )
        else:
            result = extract_dragon_tiger_signals(record, code=code, date=date)
        self._cache.put(DRAGON_TIGER_CACHE_CATEGORY, code, result)
        return result

    # ``collect`` reads naturally in background-fill orchestration code.
    collect = extract


def extract_dragon_tiger_signals(
    record: DragonTiger | None, *, code: str | None = None, date: str | None = None
) -> DragonTigerSignal:
    """Convert one normalized record into explicitly qualified observations."""

    if record is None:
        if code is None or date is None:
            raise ValueError("code and date are required when the 龙虎榜 record is absent")
        _validate_request(code, date)
        return DragonTigerSignal(date=date, code=code, status=SignalStatus.NO_DATA)
    if not isinstance(record, DragonTiger):
        raise TypeError("record must be DragonTiger or None")
    if code is not None and record.code != code:
        raise ValueError("龙虎榜 record code does not match the requested code")
    if date is not None and record.date != date:
        raise ValueError("龙虎榜 record date does not match the requested date")

    conflicting_categories = set(record.institution_seats) & set(record.hot_money_seats)
    conflicting_directions = set(record.buy_seats) & set(record.sell_seats)
    if conflicting_categories or conflicting_directions:
        names = tuple(sorted(conflicting_categories | conflicting_directions))
        return DragonTigerSignal(
            date=record.date,
            code=record.code,
            status=SignalStatus.CONFLICT,
            reasons=(record.reason,) if record.reason else (),
            source=record.source,
            source_reference=record.source_reference,
            notes=(f"conflicting seats: {', '.join(names)}",),
        )

    has_institution = (
        record.institution_net_buy is not None
        or record.institution_net_sell is not None
    )
    has_hot_money = (
        record.hot_money_net_buy is not None
        or record.hot_money_net_sell is not None
    )
    if not has_institution and not has_hot_money:
        status = SignalStatus.MISSING_FIELDS
        notes = ("institution and hot-money fields are unavailable",)
    elif not has_institution or not has_hot_money:
        status = SignalStatus.MISSING_FIELDS
        notes = (
            "institution fields are unavailable"
            if not has_institution
            else "hot-money fields are unavailable",
        )
    else:
        status = SignalStatus.OK
        notes = ()

    return DragonTigerSignal(
        date=record.date,
        code=record.code,
        status=status,
        institution_net_buy=record.institution_net_buy,
        institution_net_sell=record.institution_net_sell,
        hot_money_net_buy=record.hot_money_net_buy,
        hot_money_net_sell=record.hot_money_net_sell,
        institution_seats=record.institution_seats,
        hot_money_seats=record.hot_money_seats,
        reasons=(record.reason,) if record.reason else (),
        source=record.source,
        source_reference=record.source_reference,
        notes=notes,
    )


def _merge_records(records: Sequence[DragonTiger]) -> DragonTiger:
    if not records:
        raise ValueError("records must not be empty")
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
            dict.fromkeys(record.source_reference for record in records if record.source_reference)
        ),
    )


def _validate_request(code: str, date: str) -> None:
    if not isinstance(code, str) or not code.strip():
        raise ValueError("code must be a non-empty string")
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("date must use ISO-8601 YYYY-MM-DD format")
    try:
        calendar_date.fromisoformat(date)
    except ValueError as exc:
        raise ValueError("date must be a valid calendar date") from exc
