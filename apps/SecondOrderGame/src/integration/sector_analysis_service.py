"""Board-analysis data bundle for the PA sector tab.

Aggregates three display-only dimensions on top of the existing sector
structure:

* **资金流向** — the P0-8 capital-flow ledger window for the sector code
  (local SQLite, no network).
* **连板信息** — AkShare rise/fall limit pools intersected with the sector's
  Futu constituents (``get_sector_constituents``), producing per-stock
  limit-streak evidence.
* **龙虎榜** — the market-wide daily 龙虎榜 list intersected with the
  constituents, then per-stock enrichment (institution / hot-money net
  amounts and seats) for the few matching codes.

This service never feeds the reasoning pipeline; it only shapes UI display
data, so it does not touch the LLM injection whitelist or any prior/ledger
input.  Every network step degrades independently and is reported in
``errors``; the bundle always returns with a ``status`` the UI can render.

Code matching between sources is deliberately loose: Futu returns
``SH.600000``-style codes while AkShare returns bare ``600000``, so
:func:`_match_code` compares both directions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date as calendar_date, timedelta
from pathlib import Path
from typing import Any

from src.data.capital_flow_ledger import CapitalFlowLedger
from src.data.models import DragonTiger, LimitPoolRecord
from src.data.protocol import DataSourceError


MAX_RETRO_DAYS = 3  # 回退窗口：当日数据未出（盘中等）时向前找交易日
DEFAULT_FLOW_WINDOW_DAYS = 10


@dataclass(frozen=True, slots=True)
class CapitalFlowSnapshot:
    date: str
    main_in_flow: float
    super_in_flow: float
    big_in_flow: float
    mid_in_flow: float
    sml_in_flow: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "main_in_flow": self.main_in_flow,
            "super_in_flow": self.super_in_flow,
            "big_in_flow": self.big_in_flow,
            "mid_in_flow": self.mid_in_flow,
            "sml_in_flow": self.sml_in_flow,
        }


@dataclass(frozen=True, slots=True)
class LimitPoolSnapshot:
    date: str
    code: str
    limit_streak: int
    direction: str  # rise | fall

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "code": self.code,
            "limit_streak": self.limit_streak,
            "direction": self.direction,
        }


@dataclass(frozen=True, slots=True)
class DragonTigerSnapshot:
    date: str
    code: str
    reason: str
    net_buy_amount: float | None
    institution_net_buy: float | None
    institution_net_sell: float | None
    hot_money_net_buy: float | None
    hot_money_net_sell: float | None
    institution_seats: tuple[str, ...] = ()
    hot_money_seats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "code": self.code,
            "reason": self.reason,
            "net_buy_amount": self.net_buy_amount,
            "institution_net_buy": self.institution_net_buy,
            "institution_net_sell": self.institution_net_sell,
            "hot_money_net_buy": self.hot_money_net_buy,
            "hot_money_net_sell": self.hot_money_net_sell,
            "institution_seats": list(self.institution_seats),
            "hot_money_seats": list(self.hot_money_seats),
        }


@dataclass(frozen=True, slots=True)
class SectorAnalysisBundle:
    """One collect() outcome for one sector on one reference date."""

    sector_code: str
    sector_name: str
    date: str
    status: str  # ready | partial | unavailable
    capital_flow: tuple[CapitalFlowSnapshot, ...] = ()
    limit_pool: tuple[LimitPoolSnapshot, ...] = ()
    dragon_tiger: tuple[DragonTigerSnapshot, ...] = ()
    errors: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sector_code": self.sector_code,
            "sector_name": self.sector_name,
            "date": self.date,
            "status": self.status,
            "capital_flow": [item.to_dict() for item in self.capital_flow],
            "limit_pool": [item.to_dict() for item in self.limit_pool],
            "dragon_tiger": [item.to_dict() for item in self.dragon_tiger],
            "errors": list(self.errors),
            "raw": dict(self.raw),
        }


class SectorAnalysisService:
    """Collect the three board-analysis dimensions for one sector."""

    def __init__(
        self,
        market_source: Any,
        *,
        capital_flow_database: str | Path | None = None,
        flow_window_days: int = DEFAULT_FLOW_WINDOW_DAYS,
        max_retro_days: int = MAX_RETRO_DAYS,
    ) -> None:
        if market_source is None:
            raise TypeError("market_source must not be None")
        if not isinstance(flow_window_days, int) or isinstance(flow_window_days, bool) or flow_window_days < 1:
            raise ValueError("flow_window_days must be a positive integer")
        if not isinstance(max_retro_days, int) or isinstance(max_retro_days, bool) or max_retro_days < 0:
            raise ValueError("max_retro_days must be a non-negative integer")
        self._source = market_source
        self._capital_flow_database = capital_flow_database
        self._flow_window_days = flow_window_days
        self._max_retro_days = max_retro_days

    def collect(
        self,
        *,
        sector_code: str,
        sector_name: str = "",
        date: str | calendar_date | None = None,
    ) -> SectorAnalysisBundle:
        """Collect the bundle; never raises — every step degrades to errors."""
        code = str(sector_code or "").strip()
        if not code:
            return SectorAnalysisBundle(
                sector_code=code,
                sector_name=str(sector_name or ""),
                date=_iso(date or calendar_date.today()),
                status="unavailable",
                errors=("板块代码为空，无法采集板块分析",),
            )
        target = _iso(date or calendar_date.today())
        errors: list[str] = []

        flows = self._capital_flow(code)
        constituents = self._constituents(code, errors)

        limit_pool: tuple[LimitPoolSnapshot, ...] = ()
        dragon_tiger: tuple[DragonTigerSnapshot, ...] = ()
        if constituents:
            limit_pool = self._limit_pool(target, constituents, errors)
            dragon_tiger = self._dragon_tiger(target, constituents, errors)
        else:
            errors.append("板块成分股不可用，连板信息与龙虎榜未采集")

        has_data = bool(flows or limit_pool or dragon_tiger)
        status = "unavailable" if not has_data else "partial" if errors else "ready"
        return SectorAnalysisBundle(
            sector_code=code,
            sector_name=str(sector_name or ""),
            date=target,
            status=status,
            capital_flow=flows,
            limit_pool=limit_pool,
            dragon_tiger=dragon_tiger,
            errors=tuple(dict.fromkeys(errors)),
            raw=self._raw_payload(code, flows, limit_pool, dragon_tiger),
        )

    # -- per-dimension collectors -------------------------------------------

    def _capital_flow(self, sector_code: str) -> tuple[CapitalFlowSnapshot, ...]:
        database = self._capital_flow_database
        if database is None:
            return ()
        try:
            with CapitalFlowLedger(database) as ledger:
                flows = ledger.flows_for(sector_code)
        except Exception as exc:  # noqa: BLE001 — local ledger must not break collect
            return ()
        window = tuple(flows[-self._flow_window_days :])
        return tuple(
            CapitalFlowSnapshot(
                date=flow.date,
                main_in_flow=float(flow.main_in_flow),
                super_in_flow=float(flow.super_in_flow),
                big_in_flow=float(flow.big_in_flow),
                mid_in_flow=float(flow.mid_in_flow),
                sml_in_flow=float(flow.sml_in_flow),
            )
            for flow in window
        )

    def _constituents(
        self, sector_code: str, errors: list[str]
    ) -> tuple[str, ...]:
        try:
            values = tuple(self._source.get_sector_constituents(sector_code))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"板块成分股获取失败：{_exc_text(exc)}")
            return ()
        normalized = tuple(dict.fromkeys(str(item) for item in values))
        if not normalized:
            errors.append("板块成分股为空")
        return normalized

    def _limit_pool(
        self,
        target: str,
        constituents: Sequence[str],
        errors: list[str],
    ) -> tuple[LimitPoolSnapshot, ...]:
        for day in _retro_dates(target, self._max_retro_days):
            try:
                pool = tuple(self._source.get_limit_pool(day))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"连板池获取失败（{day}）：{_exc_text(exc)}")
                continue
            matched = _match_by_code(pool, constituents)
            if matched:
                return tuple(
                    LimitPoolSnapshot(
                        date=day,
                        code=item.code,
                        limit_streak=item.limit_streak,
                        direction=item.direction,
                    )
                    for item in matched
                )
        return ()

    def _dragon_tiger(
        self,
        target: str,
        constituents: Sequence[str],
        errors: list[str],
    ) -> tuple[DragonTigerSnapshot, ...]:
        for day in _retro_dates(target, self._max_retro_days):
            day_list = self._day_list(day, errors)
            if not day_list:
                continue
            candidates = tuple(
                record for record in day_list if _match_code(record.code, constituents)
            )
            enriched: list[DragonTigerSnapshot] = []
            for record in candidates:
                detail = self._enrich(record, day, errors)
                enriched.append(
                    DragonTigerSnapshot(
                        date=day,
                        code=detail.code,
                        reason=detail.reason or "",
                        net_buy_amount=detail.net_buy_amount,
                        institution_net_buy=detail.institution_net_buy,
                        institution_net_sell=detail.institution_net_sell,
                        hot_money_net_buy=detail.hot_money_net_buy,
                        hot_money_net_sell=detail.hot_money_net_sell,
                        institution_seats=tuple(detail.institution_seats),
                        hot_money_seats=tuple(detail.hot_money_seats),
                    )
                )
            if enriched:
                return tuple(enriched)
        return ()

    def _day_list(self, day: str, errors: list[str]) -> tuple[DragonTiger, ...]:
        getter = getattr(self._source, "get_dragon_tiger_day", None)
        if not callable(getter):
            errors.append("数据源不提供全市场龙虎榜列表，龙虎榜未采集")
            return ()
        try:
            return tuple(getter(day))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"龙虎榜列表获取失败（{day}）：{_exc_text(exc)}")
            return ()

    def _enrich(self, record: DragonTiger, day: str, errors: list[str]) -> DragonTiger:
        getter = getattr(self._source, "get_dragon_tiger", None)
        if not callable(getter):
            return record
        try:
            detail = getter(record.code, day)
        except Exception as exc:  # noqa: BLE001 — base record is still usable
            errors.append(f"龙虎榜明细获取失败（{record.code}）：{_exc_text(exc)}")
            return record
        return detail if isinstance(detail, DragonTiger) else record

    # -- raw payload ---------------------------------------------------------

    @staticmethod
    def _raw_payload(
        sector_code: str,
        flows: Sequence[CapitalFlowSnapshot],
        limit_pool: Sequence[LimitPoolSnapshot],
        dragon_tiger: Sequence[DragonTigerSnapshot],
    ) -> dict[str, Any]:
        return {
            "capital_flow": [item.to_dict() for item in flows],
            "limit_pool": [item.to_dict() for item in limit_pool],
            "dragon_tiger": [item.to_dict() for item in dragon_tiger],
            "sector_code": sector_code,
        }


def _iso(value: str | calendar_date) -> str:
    if isinstance(value, calendar_date):
        return value.isoformat()
    return str(value)[:10]


def _retro_dates(target: str, max_days: int) -> tuple[str, ...]:
    reference = calendar_date.fromisoformat(target)
    return tuple((reference - timedelta(days=offset)).isoformat() for offset in range(max_days + 1))


def _match_code(code: str, candidates: Sequence[str]) -> bool:
    """Futu (SH.600000) and AkShare (600000) codes both compare equal."""
    bare = _bare(code)
    return any(_bare(candidate) == bare for candidate in candidates)


def _bare(value: str) -> str:
    cleaned = str(value or "").strip().upper()
    for prefix in ("SH.", "SZ.", "BJ.", "HK.", "US."):
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :]
    return cleaned


def _match_by_code(
    records: Sequence[LimitPoolRecord], candidates: Sequence[str]
) -> tuple[LimitPoolRecord, ...]:
    return tuple(record for record in records if _match_code(record.code, candidates))


def _exc_text(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


__all__ = [
    "CapitalFlowSnapshot",
    "DragonTigerSnapshot",
    "LimitPoolSnapshot",
    "SectorAnalysisBundle",
    "SectorAnalysisService",
]
