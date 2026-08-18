"""Daily capital-flow collection scheduler (ROADMAP P0-8).

The capital-flow ledger is populated by a bounded daily task, not by the
analysis path.  This module mirrors the labeler nightly sweep
(``src/labeler/nightly.py``):

* ``trading_window`` backs the fixed 40-trading-day decision window out of
  the exchange calendar exposed by the market-data seam.
* ``run_daily_capital_flow`` runs one collection for the deliberately small
  registered scope (watchlist ~10-20 codes + registered sectors ~10-20),
  never scanning the whole market or active movers.
* ``run_capital_flow_catchup`` is safe to run on every startup: it checks
  per-code staleness and only queries the (code, day) targets actually
  missing from the ledger.

The task must be scheduled after the market close (cron / Windows Task
Scheduler) and must never block the analysis path.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

from src.data.capital_flow_ledger import (
    MIN_WINDOW_DAYS,
    CapitalFlowCollector,
    CapitalFlowLedger,
)
from src.data.rate_limiter import RateLimiter


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CAPITAL_FLOW_DB = ROOT / "runtime" / "data" / "capital_flow.db"
DEFAULT_SCOPE_FILE = ROOT / "runtime" / "data" / "capital_flow_scope.json"
DEFAULT_MARKET = "CN"  # Futu TradeDateMarket: CN=沪深 / HK / US

# Calendar probes used to back out N trading days; 60 calendar days usually
# hold ~43 trading days, and wider probes cover suspension-heavy windows.
_CALENDAR_PROBE_DAYS = (60, 90, 120)

# Production pacing for the Futu capital-flow endpoint (server quota is a
# small number of calls per 30s window; stay safely under it).
PRODUCTION_RATE_LIMITER = RateLimiter(max_calls=8, window_seconds=30.0)


@dataclass(frozen=True, slots=True)
class CapitalFlowDailyReport:
    """Outcome of one scheduled collection / catch-up attempt."""

    trading_date: str
    window_days: tuple[str, ...] = ()
    inserted_count: int = 0
    skipped_count: int = 0
    failures: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    caught_up: bool = False


def trading_window(
    source: Any,
    as_of: str | date,
    *,
    days: int = MIN_WINDOW_DAYS,
    market: str = DEFAULT_MARKET,
) -> tuple[str, ...]:
    """Return the last ``days`` trading days at or before ``as_of``.

    Uses the source's ``get_trading_days`` calendar (never weekdays only).
    Raises ``ValueError`` when the calendar is missing or too short.
    """
    getter = getattr(source, "get_trading_days", None)
    if not callable(getter):
        raise ValueError("market source does not expose get_trading_days")
    target = _iso(as_of)
    available: tuple[str, ...] = ()
    for probe in _CALENDAR_PROBE_DAYS:
        start = (date.fromisoformat(target) - timedelta(days=probe)).isoformat()
        try:
            raw = getter(market, start, target)
        except Exception as exc:  # noqa: BLE001 — surface as a schedule error
            raise ValueError(f"trading-day calendar unavailable: {exc}") from exc
        values = tuple(
            value if isinstance(value, str) else value.isoformat()
            for value in raw
        )
        available = tuple(sorted(dict.fromkeys(values)))
        if len(available) >= days:
            return available[-days:]
    raise ValueError(
        f"not enough trading days in calendar (need {days}, found {len(available)})"
    )


def _normalize_codes(codes: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(code.strip() for code in codes if isinstance(code, str) and code.strip())
    )


def pa_watchlist_codes(path: Path | str) -> tuple[str, ...]:
    """Read a PA watchlist.json and return normalized Futu codes (SH./SZ./HK./US.).

    The PA watchlist stores ``symbol`` plus an ``exchange`` tag (``SSE`` /
    ``SZSE`` / empty).  Codes are normalized to the Futu form the market-data
    seam expects; an empty exchange is inferred from the symbol prefix
    (5/6/9 → SH, otherwise SZ).
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取 PA 自选池 {path}: {exc}") from exc
    items = raw.get("items") if isinstance(raw, Mapping) else None
    if not isinstance(items, list):
        raise ValueError(f"PA 自选池 {path} 缺少 items 列表")
    codes: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            continue
        exchange = str(item.get("exchange") or "").strip().upper()
        raw_symbol = symbol.upper()
        if raw_symbol.startswith(("SH.", "SZ.", "HK.", "US.")):
            codes.append(raw_symbol)
            continue
        if exchange in {"SSE", "SH"}:
            codes.append(f"SH.{symbol}")
        elif exchange in {"SZSE", "SZ"}:
            codes.append(f"SZ.{symbol}")
        else:
            codes.append(
                f"{'SH' if symbol.startswith(('5', '6', '9')) else 'SZ'}.{symbol}"
            )
    return tuple(dict.fromkeys(codes))


def load_pa_settings_mapping(path: Path | str) -> dict[str, dict[str, str]]:
    """Read PA 二阶设置 ``symbol_preferences`` into usable benchmark mappings.

    Returns ``{Futu code: {"sector_code": ..., "sector_name": ...}}``.  Entries
    without a usable sector code are dropped: empty values and bare numbers
    (e.g. ``"12"``) cannot address a Futu sector index and would silently
    produce an unavailable benchmark.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取 PA 设置 {path}: {exc}") from exc
    second_order = raw.get("second_order") if isinstance(raw, Mapping) else None
    prefs = (
        second_order.get("symbol_preferences")
        if isinstance(second_order, Mapping)
        else None
    )
    if not isinstance(prefs, Mapping):
        return {}
    result: dict[str, dict[str, str]] = {}
    for raw_code, meta in prefs.items():
        if not isinstance(meta, Mapping):
            continue
        code = str(raw_code).strip().upper()
        if not code:
            continue
        if not code.startswith(("SH.", "SZ.", "HK.", "US.")):
            code = f"{'SH' if code.startswith(('5', '6', '9')) else 'SZ'}.{code}"
        sector_code = str(meta.get("sector_code") or "").strip()
        sector_name = str(meta.get("sector_name") or "").strip()
        if not sector_code or sector_code.isdigit() or "." not in sector_code:
            continue
        result[code] = {
            "sector_code": sector_code,
            "sector_name": sector_name,
        }
    return result


def save_scope(
    watchlist_codes: Iterable[str],
    sector_codes: Iterable[str],
    *,
    path: Path | str = DEFAULT_SCOPE_FILE,
) -> None:
    """Persist the collection scope so schedulers can run without arguments."""
    payload = {
        "watchlist": list(_normalize_codes(watchlist_codes)),
        "sectors": list(_normalize_codes(sector_codes)),
    }
    location = Path(path)
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_scope(
    path: Path | str = DEFAULT_SCOPE_FILE,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(watchlist, sectors)`` persisted by :func:`save_scope`."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (), ()
    if not isinstance(raw, Mapping):
        return (), ()
    watchlist = raw.get("watchlist")
    sectors = raw.get("sectors")
    return (
        _normalize_codes(watchlist if isinstance(watchlist, list | tuple) else ()),
        _normalize_codes(sectors if isinstance(sectors, list | tuple) else ()),
    )


def run_daily_capital_flow(
    source: Any,
    *,
    as_of: str | date,
    watchlist_codes: Iterable[str],
    sector_codes: Iterable[str] = (),
    sector_names: Mapping[str, str] | None = None,
    sector_focus_codes: Mapping[str, Iterable[str]] | None = None,
    ledger_database: Path | str = DEFAULT_CAPITAL_FLOW_DB,
    days: int = MIN_WINDOW_DAYS,
    market: str = DEFAULT_MARKET,
    rate_limiter: RateLimiter | None = None,
) -> CapitalFlowDailyReport:
    """Collect one bounded window for the registered scope (P0-8)."""
    target = _iso(as_of)
    try:
        window = trading_window(source, target, days=days, market=market)
    except Exception as exc:  # noqa: BLE001 — never raise into the caller
        return CapitalFlowDailyReport(
            trading_date=target,
            errors=(str(exc) or type(exc).__name__,),
        )
    watchlist = _normalize_codes(watchlist_codes)
    sectors = _normalize_codes(sector_codes)
    if not watchlist and not sectors:
        return CapitalFlowDailyReport(
            trading_date=target,
            window_days=window,
            errors=("collection scope is empty: provide watchlist and/or sector codes",),
        )
    try:
        with CapitalFlowLedger(ledger_database) as ledger:
            report = CapitalFlowCollector(
                source, ledger, rate_limiter=rate_limiter
            ).collect(
                trading_dates=window,
                watchlist_codes=watchlist,
                sector_codes=sectors,
                sector_names=sector_names,
                sector_focus_codes=sector_focus_codes,
            )
    except Exception as exc:  # noqa: BLE001
        return CapitalFlowDailyReport(
            trading_date=target,
            window_days=window,
            errors=(str(exc) or type(exc).__name__,),
        )
    return CapitalFlowDailyReport(
        trading_date=target,
        window_days=window,
        inserted_count=report.inserted_count,
        skipped_count=report.skipped_count,
        failures=tuple(
            f"{failure.code} {failure.date}: {failure.reason}"
            for failure in report.failures
        ),
    )


def run_capital_flow_catchup(
    source: Any,
    *,
    as_of: str | date,
    watchlist_codes: Iterable[str],
    sector_codes: Iterable[str] = (),
    sector_names: Mapping[str, str] | None = None,
    sector_focus_codes: Mapping[str, Iterable[str]] | None = None,
    ledger_database: Path | str = DEFAULT_CAPITAL_FLOW_DB,
    days: int = MIN_WINDOW_DAYS,
    market: str = DEFAULT_MARKET,
    rate_limiter: RateLimiter | None = None,
    progress: Callable[[str], None] | None = None,
) -> CapitalFlowDailyReport:
    """Startup catch-up: backfill the ledger window only when it is stale.

    The gate is per code: a code is current when its latest ledger date
    reaches the last trading day of the window.  Codes missing from the
    ledger (or behind the window) trigger one incremental collection, which
    queries only the absent ``(code, day)`` targets.
    """
    target = _iso(as_of)
    try:
        window = trading_window(source, target, days=days, market=market)
    except Exception as exc:  # noqa: BLE001
        return CapitalFlowDailyReport(
            trading_date=target,
            errors=(str(exc) or type(exc).__name__,),
        )
    watchlist = _normalize_codes(watchlist_codes)
    sectors = _normalize_codes(sector_codes)
    scope = (*watchlist, *sectors)
    if not scope:
        return CapitalFlowDailyReport(
            trading_date=target,
            window_days=window,
            errors=("collection scope is empty: provide watchlist and/or sector codes",),
        )
    try:
        with CapitalFlowLedger(ledger_database) as ledger:
            stale = [
                code
                for code in dict.fromkeys(scope)
                if ledger.latest_date_for(code) is None
                or ledger.latest_date_for(code) < window[-1]
            ]
    except Exception as exc:  # noqa: BLE001
        return CapitalFlowDailyReport(
            trading_date=target,
            window_days=window,
            errors=(str(exc) or type(exc).__name__,),
        )
    if not stale:
        return CapitalFlowDailyReport(
            trading_date=target,
            window_days=window,
            caught_up=True,
        )
    if progress is not None:
        progress(f"补采集资金流：{len(stale)} 个代码落后，窗口 {window[0]} → {window[-1]}")
    return run_daily_capital_flow(
        source,
        as_of=target,
        watchlist_codes=watchlist,
        sector_codes=sectors,
        sector_names=sector_names,
        sector_focus_codes=sector_focus_codes,
        ledger_database=ledger_database,
        days=days,
        market=market,
        rate_limiter=rate_limiter,
    )


def _iso(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secondordergame-capital-flow-daily",
        description="资金流向日采集调度：按收盘窗口采集自选池与在册板块的资金流并写入台账",
    )
    parser.add_argument("--date", default=date.today().isoformat(), help="目标交易日 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--watchlist", default="", help="自选池代码，逗号分隔（可选）")
    parser.add_argument("--sectors", default="", help="在册板块代码，逗号分隔，如 BK0475,BK0476（缺省读 scope 文件）")
    parser.add_argument(
        "--pa-watchlist",
        default="",
        help="PA 自选池 JSON 路径；读取其 items[].symbol 并规范为 Futu 代码作为 watchlist（可配合 --save-scope）",
    )
    parser.add_argument("--ledger-db", default=str(DEFAULT_CAPITAL_FLOW_DB))
    parser.add_argument("--scope-file", default=str(DEFAULT_SCOPE_FILE), help="采集范围文件（JSON）")
    parser.add_argument("--save-scope", action="store_true", help="把本次 --watchlist/--sectors 写入 scope 文件供自动调度复用")
    parser.add_argument("--window", type=int, default=MIN_WINDOW_DAYS, help="采集窗口交易日数（默认 40）")
    parser.add_argument("--market", default=DEFAULT_MARKET, help="交易日历市场（默认 CN=沪深）")
    args = parser.parse_args(argv)

    watchlist = _normalize_codes(args.watchlist.split(","))
    if not watchlist and args.pa_watchlist:
        watchlist = pa_watchlist_codes(args.pa_watchlist)
    sectors = _normalize_codes(args.sectors.split(","))
    if not watchlist and not sectors:
        stored_watchlist, stored_sectors = load_scope(args.scope_file)
        if stored_watchlist or stored_sectors:
            watchlist, sectors = stored_watchlist, stored_sectors
    if args.save_scope:
        save_scope(watchlist, sectors, path=args.scope_file)
    if not watchlist and not sectors:
        parser.error("--watchlist/--sectors 不能为空（也未找到 scope 文件）")

    from src.data.futu_client import FutuMarketDataSource

    source = FutuMarketDataSource()
    try:
        report = run_daily_capital_flow(
            source,
            as_of=args.date,
            watchlist_codes=watchlist,
            sector_codes=sectors,
            ledger_database=args.ledger_db,
            days=args.window,
            market=args.market,
            rate_limiter=PRODUCTION_RATE_LIMITER,
        )
    finally:
        close = getattr(source, "close", None)
        if callable(close):
            close()

    _print_report(report)
    return 0 if not report.errors else 2


def _print_report(report: CapitalFlowDailyReport) -> None:
    print(f"[capital-flow] 交易日 {report.trading_date} 窗口 {len(report.window_days)} 天")
    print(
        f"[capital-flow] 采集 {report.inserted_count} 条，跳过 {report.skipped_count} 条，"
        f"失败 {len(report.failures)} 条"
    )
    for failure in report.failures[:20]:
        print(f"  FAIL {failure}")
    if len(report.failures) > 20:
        print(f"  ... 其余 {len(report.failures) - 20} 条失败略")
    for error in report.errors:
        print(f"[capital-flow] ERROR: {error}")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_CAPITAL_FLOW_DB",
    "DEFAULT_SCOPE_FILE",
    "CapitalFlowDailyReport",
    "load_pa_settings_mapping",
    "load_scope",
    "pa_watchlist_codes",
    "run_capital_flow_catchup",
    "run_daily_capital_flow",
    "save_scope",
    "trading_window",
]
