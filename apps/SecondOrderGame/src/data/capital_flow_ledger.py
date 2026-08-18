"""Recoverable, bounded collection of daily capital-flow observations."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
import math
from numbers import Real
from pathlib import Path
import sqlite3
import time

from .models import CapitalFlow, MarketSnapshot
from .protocol import MarketDataSource
from .rate_limiter import RateLimitExceeded, RateLimiter


MIN_WINDOW_DAYS = 40
MAX_WINDOW_DAYS = 40
MAX_CODES_PER_SCOPE = 20


@dataclass(frozen=True, slots=True)
class CapitalFlowFailure:
    """A target that could not be added to the capital-flow ledger."""

    code: str
    date: str
    reason: str


@dataclass(frozen=True, slots=True)
class RepresentativeMember:
    """One frozen constituent selected for a sector decision date."""

    sector_code: str
    date: str
    code: str
    change_rate: float | None


@dataclass(frozen=True, slots=True)
class CapitalFlowCollectionReport:
    """Outcome of one bounded collection attempt."""

    inserted: tuple[CapitalFlow, ...]
    skipped_count: int
    failures: tuple[CapitalFlowFailure, ...]

    @property
    def inserted_count(self) -> int:
        return len(self.inserted)


class CapitalFlowLedger:
    """Append-only SQLite ledger keyed by ``(date, code)``."""

    def __init__(self, database: str | Path | sqlite3.Connection) -> None:
        self._owns_connection = not isinstance(database, sqlite3.Connection)
        if self._owns_connection:
            database_path = Path(database)
            database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(database_path)
        else:
            self._connection = database
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        if self._owns_connection:
            self._connection.close()

    def __enter__(self) -> "CapitalFlowLedger":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def contains(self, code: str, day: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM capital_flows WHERE date = ? AND code = ?", (day, code)
        ).fetchone()
        return row is not None

    def append(self, flow: CapitalFlow) -> bool:
        _validate_flow(flow, expected_code=flow.code, expected_date=flow.date)
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO capital_flows (
                date, code, super_in_flow, big_in_flow, mid_in_flow, sml_in_flow, main_in_flow
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                flow.date,
                flow.code,
                flow.super_in_flow,
                flow.big_in_flow,
                flow.mid_in_flow,
                flow.sml_in_flow,
                flow.main_in_flow,
            ),
        )
        self._connection.commit()
        return cursor.rowcount == 1

    def record_failure(self, failure: CapitalFlowFailure) -> None:
        """Record the current failure reason for a target, replacing older ones.

        The failures table reflects the *current* missing-data state: each
        ``(code, date)`` keeps at most one reason, so stale transient errors
        (e.g. an old rate-limit hit on a date later confirmed unavailable) do
        not accumulate alongside the up-to-date reason.
        """
        self._connection.execute(
            "DELETE FROM capital_flow_failures WHERE code = ? AND date = ?",
            (failure.code, failure.date),
        )
        self._connection.execute(
            "INSERT OR IGNORE INTO capital_flow_failures (date, code, reason) VALUES (?, ?, ?)",
            (failure.date, failure.code, failure.reason),
        )
        self._connection.commit()

    def clear_failures(self, code: str, day: str) -> None:
        """Drop stale failure records once the (code, day) data is present."""
        self._connection.execute(
            "DELETE FROM capital_flow_failures WHERE code = ? AND date = ?", (code, day)
        )
        self._connection.commit()

    def retain_window(self, trading_dates: Sequence[str]) -> None:
        """Keep only the current short decision window, never a data warehouse."""

        placeholders = ", ".join("?" for _ in trading_dates)
        self._connection.execute(
            f"DELETE FROM capital_flows WHERE date NOT IN ({placeholders})", trading_dates
        )
        self._connection.execute(
            f"DELETE FROM capital_flow_failures WHERE date NOT IN ({placeholders})",
            trading_dates,
        )
        self._connection.execute(
            f"DELETE FROM capital_flow_representatives WHERE date NOT IN ({placeholders})",
            trading_dates,
        )
        self._connection.commit()

    def count(self) -> int:
        return int(
            self._connection.execute("SELECT COUNT(*) FROM capital_flows").fetchone()[0]
        )

    def failures(self) -> tuple[CapitalFlowFailure, ...]:
        rows = self._connection.execute(
            "SELECT code, date, reason FROM capital_flow_failures ORDER BY id"
        ).fetchall()
        return tuple(
            CapitalFlowFailure(code=row["code"], date=row["date"], reason=row["reason"])
            for row in rows
        )

    def latest_date_for(self, code: str) -> str | None:
        """Most recent ledger date for one code, or None when absent."""
        row = self._connection.execute(
            "SELECT MAX(date) AS d FROM capital_flows WHERE code = ?", (code,)
        ).fetchone()
        return row["d"] if row is not None and row["d"] is not None else None

    def flows_for(self, code: str) -> tuple[CapitalFlow, ...]:
        """Return the ledger's flows for one code, oldest day first."""
        rows = self._connection.execute(
            """
            SELECT date, code, super_in_flow, big_in_flow, mid_in_flow, sml_in_flow, main_in_flow
            FROM capital_flows
            WHERE code = ?
            ORDER BY date
            """,
            (code,),
        ).fetchall()
        return tuple(
            CapitalFlow(
                date=row["date"],
                code=row["code"],
                super_in_flow=row["super_in_flow"],
                big_in_flow=row["big_in_flow"],
                mid_in_flow=row["mid_in_flow"],
                sml_in_flow=row["sml_in_flow"],
                main_in_flow=row["main_in_flow"],
            )
            for row in rows
        )

    def representative_basket_for(
        self, sector_code: str, day: str
    ) -> tuple[RepresentativeMember, ...]:
        rows = self._connection.execute(
            """
            SELECT sector_code, date, code, change_rate
            FROM capital_flow_representatives
            WHERE sector_code = ? AND date = ?
            ORDER BY selected_rank
            """,
            (sector_code, day),
        ).fetchall()
        return tuple(
            RepresentativeMember(
                sector_code=row["sector_code"],
                date=row["date"],
                code=row["code"],
                change_rate=row["change_rate"],
            )
            for row in rows
        )

    def freeze_representative_basket(
        self,
        sector_code: str,
        day: str,
        snapshots: Sequence[MarketSnapshot],
    ) -> tuple[RepresentativeMember, ...]:
        """Persist a day's basket once; repeated catch-up never re-ranks it."""
        existing = self.representative_basket_for(sector_code, day)
        if existing:
            return existing
        for rank, snapshot in enumerate(snapshots, start=1):
            self._connection.execute(
                """
                INSERT OR IGNORE INTO capital_flow_representatives
                    (sector_code, date, code, change_rate, selected_rank)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sector_code, day, snapshot.code, snapshot.change_rate, rank),
            )
        self._connection.commit()
        return self.representative_basket_for(sector_code, day)

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS capital_flows (
                date TEXT NOT NULL,
                code TEXT NOT NULL,
                super_in_flow REAL NOT NULL,
                big_in_flow REAL NOT NULL,
                mid_in_flow REAL NOT NULL,
                sml_in_flow REAL NOT NULL,
                main_in_flow REAL NOT NULL,
                PRIMARY KEY (date, code)
            );

            CREATE TABLE IF NOT EXISTS capital_flow_failures (
                id INTEGER PRIMARY KEY,
                date TEXT NOT NULL,
                code TEXT NOT NULL,
                reason TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (date, code, reason)
            );

            CREATE TABLE IF NOT EXISTS capital_flow_representatives (
                sector_code TEXT NOT NULL,
                date TEXT NOT NULL,
                code TEXT NOT NULL,
                change_rate REAL,
                selected_rank INTEGER NOT NULL,
                PRIMARY KEY (sector_code, date, code)
            );
            """
        )
        self._connection.commit()


class CapitalFlowCollector:
    """Collect the fixed 40-day decision window for a small registered scope."""

    def __init__(
        self,
        source: MarketDataSource,
        ledger: CapitalFlowLedger,
        *,
        rate_limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._source = source
        self._ledger = ledger
        self._rate_limiter = rate_limiter
        self._sleep = sleep

    def _acquire_slot(self) -> None:
        """Block until a rate-limit slot is free; never raise on throttling."""
        while self._rate_limiter is not None:
            try:
                self._rate_limiter.require()
                return
            except RateLimitExceeded as exc:
                self._sleep(exc.retry_after)

    def collect(
        self,
        *,
        trading_dates: Sequence[str],
        watchlist_codes: Iterable[str],
        sector_codes: Iterable[str] = (),
        sector_names: Mapping[str, str] | None = None,
        sector_focus_codes: Mapping[str, Iterable[str]] | None = None,
    ) -> CapitalFlowCollectionReport:
        days = _normalize_trading_dates(trading_dates)
        watchlist = _normalize_codes(watchlist_codes, "watchlist")
        sectors = _normalize_codes(sector_codes, "sector")
        names = {
            str(code).strip(): str(name).strip()
            for code, name in (sector_names or {}).items()
            if str(code).strip() in sectors and str(name).strip()
        }
        focus_codes = {
            str(code).strip(): tuple(
                dict.fromkeys(
                    str(value).strip()
                    for value in values
                    if str(value).strip()
                )
            )
            for code, values in (sector_focus_codes or {}).items()
            if str(code).strip()
        }
        codes = tuple(dict.fromkeys((*watchlist, *sectors)))
        self._ledger.retain_window(days)

        inserted: list[CapitalFlow] = []
        failures: list[CapitalFlowFailure] = []
        skipped_count = 0
        range_getter = getattr(self._source, "get_capital_flow_range", None)
        range_capable = callable(range_getter)
        for code in codes:
            needed = [day for day in days if not self._ledger.contains(code, day)]
            covered = [day for day in days if day not in needed]
            for day in covered:
                self._ledger.clear_failures(code, day)
            if not needed:
                skipped_count += len(days)
                continue
            skipped_count += len(covered)
            sector_name = names.get(code)
            if code in sectors:
                representative_handled = self._collect_representative_sector(
                    code=code,
                    days=days,
                    needed=needed,
                    focus_codes=focus_codes.get(code, ()),
                    inserted=inserted,
                    failures=failures,
                )
                if representative_handled:
                    continue
            if sector_name is not None:
                sector_getter = getattr(self._source, "get_sector_capital_flow_history", None)
                if not callable(sector_getter):
                    failure = CapitalFlowFailure(
                        code=code,
                        date=days[0],
                        reason="board capital-flow source is unavailable",
                    )
                    self._ledger.record_failure(failure)
                    failures.append(failure)
                    continue
                try:
                    self._acquire_slot()
                    ranged = {
                        flow.date: replace(flow, code=code)
                        for flow in sector_getter(sector_name)
                    }
                except Exception as exc:
                    failure = CapitalFlowFailure(
                        code=code,
                        date=days[0],
                        reason=f"board capital-flow fetch failed: {exc}",
                    )
                    self._ledger.record_failure(failure)
                    failures.append(failure)
                    continue
                for day in needed:
                    flow = ranged.get(day)
                    if flow is None:
                        failure = CapitalFlowFailure(
                            code=code, date=day, reason="capital flow is unavailable"
                        )
                        self._ledger.record_failure(failure)
                        failures.append(failure)
                        continue
                    try:
                        _validate_flow(flow, expected_code=code, expected_date=day)
                    except Exception as exc:
                        failure = CapitalFlowFailure(code=code, date=day, reason=str(exc))
                        self._ledger.record_failure(failure)
                        failures.append(failure)
                        continue
                    if self._ledger.append(flow):
                        inserted.append(flow)
                        self._ledger.clear_failures(code, day)
                    else:
                        skipped_count += 1
                continue
            if range_capable:
                try:
                    self._acquire_slot()
                    ranged = {
                        flow.date: flow
                        for flow in range_getter(code, days[0], days[-1])
                    }
                except Exception as exc:
                    ranged = None
                    failure = CapitalFlowFailure(
                        code=code, date=days[0], reason=f"range fetch failed: {exc}"
                    )
                    self._ledger.record_failure(failure)
                    failures.append(failure)
                if ranged is not None:
                    for day in needed:
                        flow = ranged.get(day)
                        if flow is None:
                            failure = CapitalFlowFailure(
                                code=code, date=day, reason="capital flow is unavailable"
                            )
                            self._ledger.record_failure(failure)
                            failures.append(failure)
                            continue
                        try:
                            _validate_flow(flow, expected_code=code, expected_date=day)
                        except Exception as exc:
                            failure = CapitalFlowFailure(
                                code=code, date=day, reason=str(exc)
                            )
                            self._ledger.record_failure(failure)
                            failures.append(failure)
                            continue
                        if self._ledger.append(flow):
                            inserted.append(flow)
                            self._ledger.clear_failures(code, day)
                        else:
                            skipped_count += 1
                    continue
            # Per-day fallback (no range endpoint, or the range fetch failed).
            for day in needed:
                try:
                    self._acquire_slot()
                    flow = self._source.get_capital_flow(code, day)
                    if flow is None:
                        raise ValueError("capital flow is unavailable")
                    _validate_flow(flow, expected_code=code, expected_date=day)
                    if self._ledger.append(flow):
                        inserted.append(flow)
                        self._ledger.clear_failures(code, day)
                    else:
                        skipped_count += 1
                except Exception as exc:
                    failure = CapitalFlowFailure(code=code, date=day, reason=str(exc))
                    self._ledger.record_failure(failure)
                    failures.append(failure)

        return CapitalFlowCollectionReport(
            inserted=tuple(inserted),
            skipped_count=skipped_count,
            failures=tuple(failures),
        )

    def _collect_representative_sector(
        self,
        *,
        code: str,
        days: Sequence[str],
        needed: Sequence[str],
        focus_codes: Sequence[str],
        inserted: list[CapitalFlow],
        failures: list[CapitalFlowFailure],
    ) -> bool:
        """Aggregate daily flows for a frozen 3-rise/2-fall basket.

        A basket is created only for the newest day. Older dates are filled
        only after their own daily basket was captured, so a current ranking
        cannot leak backwards into the 40-day window.
        """
        constituents_getter = getattr(self._source, "get_sector_constituents", None)
        snapshots_getter = getattr(self._source, "get_market_snapshots", None)
        if not callable(constituents_getter) or not callable(snapshots_getter):
            return False
        try:
            constituents = tuple(dict.fromkeys(str(item).strip() for item in constituents_getter(code)))
            if not constituents:
                return False
            current_day = days[-1]
            basket = self._ledger.representative_basket_for(code, current_day)
            if not basket:
                members = tuple(dict.fromkeys((*constituents, *focus_codes)))
                snapshots = tuple(snapshots_getter(members))
                selected = select_representative_members(snapshots, focus_codes)
                if not selected:
                    raise ValueError("板块没有可用的涨跌幅快照")
                basket = self._ledger.freeze_representative_basket(code, current_day, selected)
            basket_by_day = {
                day: self._ledger.representative_basket_for(code, day)
                for day in needed
            }
            basket_by_day = {day: value for day, value in basket_by_day.items() if value}
            if not basket_by_day:
                return True
            members = tuple(dict.fromkeys(
                member.code for value in basket_by_day.values() for member in value
            ))
            ranges: dict[str, dict[str, CapitalFlow]] = {}
            range_getter = getattr(self._source, "get_capital_flow_range", None)
            lower, upper = min(basket_by_day), max(basket_by_day)
            for member_code in members:
                if callable(range_getter):
                    self._acquire_slot()
                    ranges[member_code] = {
                        flow.date: flow
                        for flow in range_getter(member_code, lower, upper)
                    }
                else:
                    values: dict[str, CapitalFlow] = {}
                    for day in basket_by_day:
                        self._acquire_slot()
                        flow = self._source.get_capital_flow(member_code, day)
                        if flow is not None:
                            values[day] = flow
                    ranges[member_code] = values
            for day, selected_members in basket_by_day.items():
                flows = [ranges[item.code].get(day) for item in selected_members]
                if any(flow is None for flow in flows):
                    failure = CapitalFlowFailure(
                        code=code,
                        date=day,
                        reason="代表成分股资金流不完整",
                    )
                    self._ledger.record_failure(failure)
                    failures.append(failure)
                    continue
                assert all(flow is not None for flow in flows)
                aggregate = CapitalFlow(
                    date=day,
                    code=code,
                    super_in_flow=sum(flow.super_in_flow for flow in flows),
                    big_in_flow=sum(flow.big_in_flow for flow in flows),
                    mid_in_flow=sum(flow.mid_in_flow for flow in flows),
                    sml_in_flow=sum(flow.sml_in_flow for flow in flows),
                    main_in_flow=sum(flow.main_in_flow for flow in flows),
                )
                if self._ledger.append(aggregate):
                    inserted.append(aggregate)
                    self._ledger.clear_failures(code, day)
            return True
        except Exception as exc:
            failure = CapitalFlowFailure(
                code=code,
                date=days[-1],
                reason=f"代表成分股资金流采集失败: {exc}",
            )
            self._ledger.record_failure(failure)
            failures.append(failure)
            return True


def _normalize_trading_dates(trading_dates: Sequence[str]) -> tuple[str, ...]:
    days = tuple(trading_dates)
    if not MIN_WINDOW_DAYS <= len(days) <= MAX_WINDOW_DAYS:
        if MIN_WINDOW_DAYS == MAX_WINDOW_DAYS:
            raise ValueError(
                f"capital-flow collection window must contain exactly {MIN_WINDOW_DAYS} trading days"
            )
        raise ValueError(
            f"capital-flow collection window must contain between {MIN_WINDOW_DAYS} and {MAX_WINDOW_DAYS} trading days"
        )
    if len(set(days)) != len(days):
        raise ValueError("trading_dates must not contain duplicates")

    for day in days:
        if not isinstance(day, str):
            raise ValueError("trading_dates must use ISO-8601 date strings")
        try:
            parsed = date.fromisoformat(day)
        except ValueError as exc:
            raise ValueError(f"invalid trading date: {day!r}") from exc
        if parsed.isoformat() != day:
            raise ValueError(f"trading date must be ISO-8601: {day!r}")
    if tuple(sorted(days)) != days:
        raise ValueError("trading_dates must be ordered from oldest to newest")
    return days


def _normalize_codes(codes: Iterable[str], label: str) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(code.strip() for code in codes))
    if any(not code for code in normalized):
        raise ValueError(f"{label} codes must be non-empty strings")
    if len(normalized) > MAX_CODES_PER_SCOPE:
        raise ValueError(f"{label} scope accepts at most {MAX_CODES_PER_SCOPE} codes")
    return normalized


def select_representative_members(
    snapshots: Iterable[MarketSnapshot], focus_codes: Iterable[str] = ()
) -> tuple[MarketSnapshot, ...]:
    """Select the frozen board sample: three strongest, two weakest, focus.

    Missing or duplicate snapshots are ignored.  The focus code is appended
    after the two directional groups, and deduplication keeps the basket at
    five constituents plus any distinct associated stock.
    """
    by_code: dict[str, MarketSnapshot] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, MarketSnapshot) or not snapshot.code.strip():
            continue
        if not math.isfinite(snapshot.change_rate):
            continue
        by_code.setdefault(snapshot.code, snapshot)
    ranked = tuple(sorted(by_code.values(), key=lambda item: (-item.change_rate, item.code)))
    strongest = ranked[:3]
    selected_codes = {item.code for item in strongest}
    weakest = tuple(
        item
        for item in sorted(ranked, key=lambda entry: (entry.change_rate, entry.code))
        if item.code not in selected_codes
    )[:2]
    ordered = [*strongest, *weakest]
    for code in focus_codes:
        snapshot = by_code.get(str(code).strip())
        if snapshot is not None and snapshot.code not in {item.code for item in ordered}:
            ordered.append(snapshot)
    return tuple(ordered)


def _validate_flow(flow: CapitalFlow, *, expected_code: str, expected_date: str) -> None:
    if flow.code != expected_code or flow.date != expected_date:
        raise ValueError("capital flow does not match its requested code and date")
    for field in (
        "super_in_flow",
        "big_in_flow",
        "mid_in_flow",
        "sml_in_flow",
        "main_in_flow",
    ):
        value = getattr(flow, field)
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
            raise ValueError(f"capital flow field {field} must be a finite number")
