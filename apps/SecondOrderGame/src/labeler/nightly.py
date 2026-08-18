"""Nightly labeler sweep: accumulate the post-hoc label flow (ADR-0007).

Every evening the labelers scan history with their look-ahead windows and
emit a delayed (5/10 trading day) label stream.  This module is the durable
production home for that sweep: it pulls OHLCV from the market source, runs
the frozen sector v1 / stock labelers into :class:`LabelLedger`, runs the
sector v2 shadow labeler into :class:`ShadowStateStore`, attempts a staged
v2 cutover when readiness gates pass, and reconciles C confusion counts
from live LLM observations.

Day-0 principle (ADR-0007): every day not labeled is a day of labels lost
forever.  The sweep must be scheduled nightly (cron / Windows Task
Scheduler) and must never block on optional materials.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from src.data.capital_flow_ledger import CapitalFlowLedger
from src.data.models import Bar, CapitalFlow
from src.labeler.behavior_counts import BehaviorCountStore
from src.labeler.confusion_counts import ConfusionCountStore
from src.labeler.ledger import LabelLedger
from src.labeler.sector_labeler import SectorLabeler
from src.labeler.sector_labeler_v2 import ConstituentDailyObservation, SectorLabelerV2, TrendState
from src.labeler.shadow_cutover import ShadowCutoverManager, ShadowStateStore
from src.labeler.stock_labeler import StockLabeler


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = ROOT / "runtime" / "labeler" / "labels.db"
DEFAULT_SHADOW = ROOT / "runtime" / "labeler" / "shadow_v2.db"
DEFAULT_PRODUCTION = ROOT / "runtime" / "labeler" / "production"
DEFAULT_REPORTS = ROOT / "runtime" / "labeler" / "reports"
DEFAULT_CONFUSION = ROOT / "runtime" / "labeler" / "confusion.db"
DEFAULT_BEHAVIOR = ROOT / "runtime" / "labeler" / "behavior.db"

# Look-ahead window of the sector labeler (10 trading days) plus the stock
# labeler (5).  We pull a padded calendar range so rolling features and
# suspension days never starve the forward window.
LOOKAHEAD_BUFFER_DAYS = 30
HISTORY_PAD_DAYS = 400

# Startup catch-up window: a sector is "caught up" when its latest labeled
# day is within this many trading days before today.  10 (sector look-ahead)
# + 5 (stock look-ahead) + 3 buffer.
CATCHUP_LABEL_WINDOW = 18


@dataclass(frozen=True, slots=True)
class SectorRunReport:
    sector_code: str
    sector_name: str
    sector_labeled: int
    stock_labeled: int
    stock_unavailable: int
    v2_status: str | None
    v2_reason: str | None


@dataclass(frozen=True, slots=True)
class NightlyLabelingReport:
    trading_date: str
    sector_runs: tuple[SectorRunReport, ...] = ()
    ledger_rows: int = 0
    cutover_status: str = "not_attempted"
    cutover_reason: str = ""
    confusion_increments: dict[str, int] = field(default_factory=dict)
    behavior_increments: dict[str, int] = field(default_factory=dict)
    errors: tuple[str, ...] = ()


def run_nightly(
    source: Any,
    *,
    trading_date: str | date,
    sectors: Mapping[str, str],
    ledger_database: Path | str = DEFAULT_LEDGER,
    shadow_database: Path | str = DEFAULT_SHADOW,
    production_directory: Path | str = DEFAULT_PRODUCTION,
    report_directory: Path | str = DEFAULT_REPORTS,
    confusion_database: Path | str | None = DEFAULT_CONFUSION,
    behavior_database: Path | str | None = DEFAULT_BEHAVIOR,
    v2_observer: Callable[[str, str], Sequence[ConstituentDailyObservation]] | None = None,
    trend_state_provider: Callable[[str, str, str, str], TrendState] | None = None,
    llm_observations: Iterable[tuple[str, str, str]] | None = None,
    sector_labeler: SectorLabeler | None = None,
    stock_labeler: StockLabeler | None = None,
    history_start: str | None = None,
    capital_flow_database: Path | str | None = None,
    watchlist: Mapping[str, Mapping[str, str]] | None = None,
) -> NightlyLabelingReport:
    """Run one nightly sweep.

    Two modes share the same tail (v2 cutover + C/behavior reconcile):

    * **sector mode** (default): ``sectors`` maps normalized sector codes to
      display names; each sector's index and its constituents are labeled.
    * **watchlist mode**: ``watchlist`` maps Futu codes to
      ``{"name", "sector_code", "sector_name"}``.  The labeled stock scope is
      the watchlist itself — the program cannot reliably determine sector
      membership, so every watchlist symbol with a usable associated sector
      benchmark gets labeled, and its capital-flow participant judgment runs
      from the ledger.  Entries without a usable ``sector_code`` are reported
      and skipped.  The associated sector indices are labeled too (sector v1 /
      v2 shadow / C counts), giving the sector layer its scope.

    v2 shadow data and LLM observations are optional; when absent the sweep
    still accumulates v1 labels (ADR-0007 Day-0 requirement).
    ``capital_flow_database`` optionally points at the capital-flow ledger
    (ROADMAP P0-8); when set, its flows are fed to the stock labeler's
    participant classification.  A missing or stale ledger never aborts the
    sweep.
    """
    target = _iso(trading_date)
    start = history_start or (date.fromisoformat(target) - timedelta(days=HISTORY_PAD_DAYS)).isoformat()
    errors: list[str] = []
    sector_runs: list[SectorRunReport] = []
    total_rows = 0
    end_buffer = (date.fromisoformat(target) + timedelta(days=LOOKAHEAD_BUFFER_DAYS)).isoformat()

    sector_lab = sector_labeler or SectorLabeler()
    stock_lab = stock_labeler or StockLabeler()
    v2_lab = SectorLabelerV2()

    with LabelLedger(ledger_database) as ledger:
        if watchlist is not None:
            sector_runs, total_rows, effective_sectors = _sweep_watchlist(
                source,
                trading_date=target,
                start=start,
                end_buffer=end_buffer,
                watchlist=watchlist,
                ledger=ledger,
                shadow_database=shadow_database,
                sector_labeler=sector_lab,
                stock_labeler=stock_lab,
                v2_labeler=v2_lab,
                v2_observer=v2_observer,
                trend_state_provider=trend_state_provider,
                capital_flow_database=capital_flow_database,
                errors=errors,
            )
        else:
            sector_runs, total_rows, effective_sectors = _sweep_sectors(
                source,
                trading_date=target,
                start=start,
                end_buffer=end_buffer,
                sectors=sectors,
                ledger=ledger,
                shadow_database=shadow_database,
                sector_labeler=sector_lab,
                stock_labeler=stock_lab,
                v2_labeler=v2_lab,
                v2_observer=v2_observer,
                trend_state_provider=trend_state_provider,
                capital_flow_database=capital_flow_database,
                errors=errors,
            )

    cutover_status = "not_attempted"
    cutover_reason = ""
    try:
        cutover = _attempt_v2_cutover(
            source,
            sectors=effective_sectors,
            shadow_database=shadow_database,
            production_directory=production_directory,
            report_directory=report_directory,
            ledger_database=ledger_database,
            stock_labeler=stock_lab,
            trading_date=target,
        )
        cutover_status = cutover.status
        cutover_reason = cutover.reason
    except Exception as exc:  # noqa: BLE001
        errors.append(f"v2 cutover: {exc}")

    confusion_increments: dict[str, int] = {}
    if confusion_database is not None:
        try:
            confusion_increments = _reconcile_confusion(
                confusion_database=confusion_database,
                ledger_database=ledger_database,
                llm_observations=llm_observations,
                sector_labeler=sector_lab,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"confusion reconcile: {exc}")

    behavior_increments: dict[str, int] = {}
    if behavior_database is not None:
        try:
            behavior_increments = _reconcile_behavior(
                behavior_database=behavior_database,
                ledger_database=ledger_database,
                sector_labeler=sector_lab,
                stock_labeler=stock_lab,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"behavior reconcile: {exc}")

    return NightlyLabelingReport(
        trading_date=target,
        sector_runs=tuple(sector_runs),
        ledger_rows=total_rows,
        cutover_status=cutover_status,
        cutover_reason=cutover_reason,
        confusion_increments=confusion_increments,
        behavior_increments=behavior_increments,
        errors=tuple(errors),
    )


def _sweep_sectors(
    source: Any,
    *,
    trading_date: str,
    start: str,
    end_buffer: str,
    sectors: Mapping[str, str],
    ledger: LabelLedger,
    shadow_database: Path | str,
    sector_labeler: SectorLabeler,
    stock_labeler: StockLabeler,
    v2_labeler: SectorLabelerV2,
    v2_observer: Callable[[str, str], Sequence[ConstituentDailyObservation]] | None,
    trend_state_provider: Callable[[str, str, str, str], TrendState] | None,
    capital_flow_database: Path | str | None,
    errors: list[str],
) -> tuple[list[SectorRunReport], int, tuple[str, ...]]:
    """Sector mode: label each registered sector index and its constituents."""
    sector_runs: list[SectorRunReport] = []
    total_rows = 0
    for sector_code, sector_name in sectors.items():
        try:
            report = _label_sector(
                source,
                sector_code=sector_code,
                sector_name=sector_name,
                trading_date=trading_date,
                start=start,
                end_buffer=end_buffer,
                ledger=ledger,
                shadow_database=shadow_database,
                sector_labeler=sector_labeler,
                stock_labeler=stock_labeler,
                v2_labeler=v2_labeler,
                v2_observer=v2_observer,
                trend_state_provider=trend_state_provider,
                capital_flow_database=capital_flow_database,
            )
            sector_runs.append(report)
            total_rows += report.sector_labeled + report.stock_labeled
        except Exception as exc:  # noqa: BLE001 — one sector must not abort the sweep
            errors.append(f"{sector_code}: {exc}")
            sector_runs.append(
                SectorRunReport(sector_code, sector_name, 0, 0, 0, None, str(exc) or type(exc).__name__)
            )
    return sector_runs, total_rows, tuple(sectors)


def _sweep_watchlist(
    source: Any,
    *,
    trading_date: str,
    start: str,
    end_buffer: str,
    watchlist: Mapping[str, Mapping[str, str]],
    ledger: LabelLedger,
    shadow_database: Path | str,
    sector_labeler: SectorLabeler,
    stock_labeler: StockLabeler,
    v2_labeler: SectorLabelerV2,
    v2_observer: Callable[[str, str], Sequence[ConstituentDailyObservation]] | None,
    trend_state_provider: Callable[[str, str, str, str], TrendState] | None,
    capital_flow_database: Path | str | None,
    errors: list[str],
) -> tuple[list[SectorRunReport], int, tuple[str, ...]]:
    """Watchlist mode: label every watchlist symbol against its sector benchmark.

    The labeled stock scope is the watchlist itself; each symbol needs a
    usable associated ``sector_code`` as its benchmark (PA 二阶设置
    ``symbol_preferences``).  Entries without one are reported and skipped —
    the program cannot determine sector membership reliably, so the mapping is
    the authority.  Associated sector indices are labeled too (sector v1),
    giving the sector layer and the C/behavior reconciles their scope.
    """
    entries = [
        (code, _watchlist_meta(meta))
        for code, meta in watchlist.items()
        if isinstance(meta, Mapping)
    ]
    sector_index: dict[str, str] = {}
    for _, meta in entries:
        if meta["sector_code"] is not None:
            sector_index.setdefault(
                meta["sector_code"], meta["sector_name"] or meta["sector_code"]
            )
    # 1. Associated sector indices -> sector v1 labels (+ optional v2 shadow).
    for sector_code, sector_name in sector_index.items():
        try:
            _label_sector_index(
                source,
                sector_code=sector_code,
                sector_name=sector_name,
                trading_date=trading_date,
                start=start,
                end_buffer=end_buffer,
                ledger=ledger,
                shadow_database=shadow_database,
                sector_labeler=sector_labeler,
                v2_labeler=v2_labeler,
                v2_observer=v2_observer,
                trend_state_provider=trend_state_provider,
            )
        except Exception as exc:  # noqa: BLE001 — one sector must not abort the sweep
            errors.append(f"{sector_code}: {exc}")
    # 2. Watchlist symbols -> stock labels (benchmark = associated sector).
    runs: list[SectorRunReport] = []
    total_rows = 0
    flows_reader = _capital_flows_reader(capital_flow_database)
    for code, meta in entries:
        sector_code = meta["sector_code"]
        if sector_code is None:
            runs.append(
                SectorRunReport(code, meta["name"] or code, 0, 0, 1, None, "sector_mapping_missing")
            )
            continue
        try:
            stock_bars = _bars_frame(source.get_kline(code, "K_DAY", start, end_buffer))
            if stock_bars.empty:
                runs.append(SectorRunReport(code, meta["name"] or code, 0, 0, 1, None, "no stock kline"))
                continue
            sector_bars = _bars_frame(source.get_kline(sector_code, "K_DAY", start, end_buffer))
            if sector_bars.empty:
                runs.append(
                    SectorRunReport(code, meta["name"] or code, 0, 0, 1, None, "no sector benchmark kline")
                )
                continue
            result = stock_labeler.label(stock_bars, sector_bars, flows_reader(code))
            rows = result.rows[result.rows["date"] <= trading_date]
            ledger.record_stock_labels(
                code,
                rows.to_dict("records"),
                feature_rows=result.features[result.rows["date"] <= trading_date].to_dict("records"),
                sector_code=sector_code,
            )
            labeled = int(rows["status"].eq("labeled").sum())
            runs.append(SectorRunReport(code, meta["name"] or code, 0, labeled, 0, None, None))
            total_rows += labeled
        except Exception as exc:  # noqa: BLE001 — one symbol must not abort the sweep
            runs.append(
                SectorRunReport(
                    code, meta["name"] or code, 0, 0, 1, None, str(exc) or type(exc).__name__
                )
            )
    return runs, total_rows, tuple(sector_index)


def _watchlist_meta(meta: Mapping[str, Any]) -> dict[str, str | None]:
    """Normalize one watchlist entry's mapping to name/sector fields."""
    name = str(meta.get("name") or "").strip()
    sector_code = str(meta.get("sector_code") or "").strip() or None
    sector_name = str(meta.get("sector_name") or "").strip() or None
    if sector_code is not None and (sector_code.isdigit() or "." not in sector_code):
        sector_code = None
    return {"name": name, "sector_code": sector_code, "sector_name": sector_name}


# ---------------------------------------------------------------------------
# per-sector labeling
# ---------------------------------------------------------------------------


def _label_sector_index(
    source: Any,
    *,
    sector_code: str,
    sector_name: str,
    trading_date: str,
    start: str,
    end_buffer: str,
    ledger: LabelLedger,
    shadow_database: Path | str,
    sector_labeler: SectorLabeler,
    v2_labeler: SectorLabelerV2,
    v2_observer: Callable[[str, str], Sequence[ConstituentDailyObservation]] | None,
    trend_state_provider: Callable[[str, str, str, str], TrendState] | None,
) -> tuple[pd.DataFrame, int, str | None, str | None]:
    """Label one sector index (v1 labels + optional v2 shadow).

    Returns ``(sector_bars, sector_labeled_count, v2_status, v2_reason)`` so
    sector-mode callers can reuse the bars as the constituent benchmark.
    """
    sector_bars = _bars_frame(source.get_kline(sector_code, "K_DAY", start, end_buffer))
    if sector_bars.empty:
        raise ValueError("板块指数无 K_DAY 数据")
    sector_result = sector_labeler.label(sector_bars)
    sector_rows = sector_result.rows[sector_result.rows["date"] <= trading_date]
    ledger.record_sector_labels(
        sector_code,
        sector_rows.to_dict("records"),
        feature_rows=sector_result.features[sector_result.rows["date"] <= trading_date].to_dict("records"),
    )

    v2_status: str | None = None
    v2_reason: str | None = None
    if v2_observer is not None and callable(v2_observer):
        getter = getattr(source, "get_sector_constituents", None)
        constituent_codes = tuple(getter(sector_code)) if callable(getter) else ()
        observations = tuple(v2_observer(sector_code, trading_date))
        trend = (
            trend_state_provider(sector_code, trading_date, start, end_buffer)
            if trend_state_provider is not None
            else TrendState.UP_CONFIRMED
        )
        v2_label = v2_labeler.label_day(
            sector_code=sector_code,
            trading_date=trading_date,
            futu_constituent_codes=constituent_codes,
            akshare_observations=observations,
            trend_state=trend,
        )
        store = ShadowStateStore(shadow_database)
        store.record_label(v2_label, structural_error=v2_label.status != "labeled")
        store.close()
        v2_status = v2_label.status
        v2_reason = v2_label.reason

    return sector_bars, int(sector_rows["status"].eq("labeled").sum()), v2_status, v2_reason


def _label_sector(
    source: Any,
    *,
    sector_code: str,
    sector_name: str,
    trading_date: str,
    start: str,
    end_buffer: str,
    ledger: LabelLedger,
    shadow_database: Path | str,
    sector_labeler: SectorLabeler,
    stock_labeler: StockLabeler,
    v2_labeler: SectorLabelerV2,
    v2_observer: Callable[[str, str], Sequence[ConstituentDailyObservation]] | None,
    trend_state_provider: Callable[[str, str, str, str], TrendState] | None,
    capital_flow_database: Path | str | None = None,
) -> SectorRunReport:
    # 1. Sector index OHLCV -> sector v1 labels + v2 shadow.
    sector_bars, sector_labeled, v2_status, v2_reason = _label_sector_index(
        source,
        sector_code=sector_code,
        sector_name=sector_name,
        trading_date=trading_date,
        start=start,
        end_buffer=end_buffer,
        ledger=ledger,
        shadow_database=shadow_database,
        sector_labeler=sector_labeler,
        v2_labeler=v2_labeler,
        v2_observer=v2_observer,
        trend_state_provider=trend_state_provider,
    )

    # 2. Constituent stock OHLCV -> stock labels (best effort per stock)
    getter = getattr(source, "get_sector_constituents", None)
    constituent_codes = tuple(getter(sector_code)) if callable(getter) else ()
    stock_labeled = 0
    stock_unavailable = 0
    flows_reader = _capital_flows_reader(capital_flow_database)
    for code in constituent_codes:
        try:
            stock_bars = _bars_frame(source.get_kline(code, "K_DAY", start, end_buffer))
            if stock_bars.empty:
                stock_unavailable += 1
                continue
            stock_result = stock_labeler.label(
                stock_bars,
                sector_bars,
                flows_reader(code),
            )
            rows = stock_result.rows[stock_result.rows["date"] <= trading_date]
            ledger.record_stock_labels(
                code,
                rows.to_dict("records"),
                feature_rows=stock_result.features[stock_result.rows["date"] <= trading_date].to_dict("records"),
                sector_code=sector_code,
            )
            stock_labeled += int(rows["status"].eq("labeled").sum())
        except Exception:  # noqa: BLE001 — one stock must not abort the sector
            stock_unavailable += 1

    return SectorRunReport(
        sector_code=sector_code,
        sector_name=sector_name,
        sector_labeled=sector_labeled,
        stock_labeled=stock_labeled,
        stock_unavailable=stock_unavailable,
        v2_status=v2_status,
        v2_reason=v2_reason,
    )


def shadow_database_for(sector_code: str) -> Path:
    """Per-sector shadow db path (registry-neutral, sector keyed)."""
    return DEFAULT_SHADOW


# ---------------------------------------------------------------------------
# v2 cutover
# ---------------------------------------------------------------------------


def _attempt_v2_cutover(
    source: Any,
    *,
    sectors: Sequence[str],
    shadow_database: Path | str,
    production_directory: Path | str,
    report_directory: Path | str,
    ledger_database: Path | str,
    stock_labeler: StockLabeler,
    trading_date: str,
) -> Any:
    from src.labeler.shadow_cutover import CutoverResult, ShadowCutoverManager

    store = ShadowStateStore(shadow_database)
    manager = ShadowCutoverManager(store, production_directory, report_directory)
    v2_hash = SectorLabelerV2().rule_hash

    def relabel_history() -> Mapping[str, Any]:
        """Full relabel from the persisted sector labels (v1-style rows)."""
        with LabelLedger(ledger_database) as ledger:
            labels: dict[str, list[str]] = {}
            for sector in sectors:
                rows = ledger.sector_labels(sector_code=sector, status="labeled")
                labels[sector] = [row.label for row in rows if row.label is not None]
            return labels

    def rebuild_c_counts(labels: Mapping[str, Any]) -> Mapping[str, Any]:
        """Rebuild v2-independent C counts from the relabeled history."""
        counts: dict[tuple[str, str], int] = {}
        for sector, values in labels.items():
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, str) or value not in {"冰点", "启动", "发酵", "高潮", "退潮"}:
                    continue
                key = ("true_" + value, "llm_" + value)
                counts[key] = counts.get(key, 0) + 1
        return {f"{true}|{llm}": count for (true, llm), count in counts.items()}

    return manager.attempt(
        sectors,
        rule_hash=v2_hash,
        relabel_history=relabel_history,
        rebuild_c_counts=rebuild_c_counts,
    )


# ---------------------------------------------------------------------------
# C confusion reconcile
# ---------------------------------------------------------------------------


def _reconcile_confusion(
    *,
    confusion_database: Path | str,
    ledger_database: Path | str,
    llm_observations: Iterable[tuple[str, str, str]] | None,
    sector_labeler: SectorLabeler,
) -> dict[str, int]:
    with ConfusionCountStore(confusion_database) as counts_store:
        if llm_observations is not None:
            for sector_code, trading_date, llm_label in llm_observations:
                counts_store.record_llm_observation(sector_code, trading_date, llm_label)
        with LabelLedger(ledger_database) as ledger:
            return counts_store.reconcile(ledger, rule_hash=sector_labeler.rule_hash)


def _reconcile_behavior(
    *,
    behavior_database: Path | str,
    ledger_database: Path | str,
    sector_labeler: SectorLabeler,
    stock_labeler: StockLabeler,
) -> dict[str, int]:
    with BehaviorCountStore(behavior_database) as store:
        with LabelLedger(ledger_database) as ledger:
            return store.reconcile(
                ledger,
                cycle_rule_hash=sector_labeler.rule_hash,
                behavior_rule_hash=stock_labeler.rule_hash,
            )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LabelerCatchUpReport:
    """Result of an automatic startup catch-up sweep."""

    as_of: str
    caught_up_to: str | None
    missed_dates: tuple[str, ...] = ()
    ran_sweeps: int = 0
    skipped: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def needs_action(self) -> bool:
        return bool(self.missed_dates) or bool(self.ran_sweeps)


def compute_labeler_gap(
    ledger: LabelLedger,
    *,
    sectors: Mapping[str, str],
    as_of: str,
    rule_hash: str,
    label_window_days: int = CATCHUP_LABEL_WINDOW,
) -> tuple[str | None, tuple[str, ...]]:
    """Return ``(caught_up_to, missed_dates)`` for the given sectors.

    A sector is considered caught up when it has a labeled day within the
    ``label_window_days`` trading-day window before ``as_of``.  The labeler
    look-ahead (sector 10 / stock 5 trading days) plus a buffer means the most
    recent reliably labelable day lags the calendar; that lag is exactly what
    this window expresses.  ``missed_dates`` are the trading days strictly
    after the latest labeled day, up to ``as_of - window``.
    """
    reference = date.fromisoformat(as_of)
    cutoff = _shift_trading_days(reference, -label_window_days)
    caught_up_to: str | None = None
    missed: set[str] = set()
    for sector_code in sectors:
        latest = ledger.latest_labeled_date("sector", sector_code, rule_hash=rule_hash)
        if latest is None:
            # No label yet: the whole window is missed, capped at the cutoff.
            missed.add(cutoff.isoformat())
            continue
        latest_date = date.fromisoformat(latest)
        if latest_date > reference:
            continue
        if latest_date >= cutoff:
            candidate = latest
            if caught_up_to is None or latest > caught_up_to:
                caught_up_to = latest
            continue
        # Behind the window: every trading day after latest up to cutoff is missed.
        for day in _trading_days_between(latest_date, cutoff):
            missed.add(day.isoformat())
    return caught_up_to, tuple(sorted(missed))


def run_labeler_catchup(
    source: Any,
    *,
    trading_date: str | date,
    sectors: Mapping[str, str],
    ledger_database: Path | str = DEFAULT_LEDGER,
    shadow_database: Path | str = DEFAULT_SHADOW,
    production_directory: Path | str = DEFAULT_PRODUCTION,
    report_directory: Path | str = DEFAULT_REPORTS,
    confusion_database: Path | str | None = DEFAULT_CONFUSION,
    behavior_database: Path | str | None = DEFAULT_BEHAVIOR,
    llm_observations: Iterable[tuple[str, str, str]] | None = None,
    label_window_days: int = CATCHUP_LABEL_WINDOW,
    progress: Callable[[str], None] | None = None,
    capital_flow_database: Path | str | None = None,
    watchlist: Mapping[str, Mapping[str, str]] | None = None,
) -> LabelerCatchUpReport:
    """Catch up any labeled entity that fell behind the label window.

    In sector mode (default) the gap scan is per registered sector; in
    watchlist mode (``watchlist`` provided) it is per watchlist symbol, and
    the sweep runs in watchlist mode.  Safe to run on every startup:
    already-caught-up entities are skipped without network work.
    """
    target = _iso(trading_date)
    sector_lab = SectorLabeler()
    stock_lab = StockLabeler()
    errors: list[str] = []
    missed: tuple[str, ...] = ()
    caught_up_to: str | None = None
    ran = 0
    try:
        with LabelLedger(ledger_database) as ledger:
            if watchlist is not None:
                latest_by_code = ledger.latest_labeled_dates(
                    "stock",
                    rule_hash=stock_lab.rule_hash,
                    entities=tuple(watchlist),
                )
                cutoff = _shift_trading_days(date.fromisoformat(target), -label_window_days)
                pending: set[str] = set()
                for code, latest in latest_by_code.items():
                    if latest is None:
                        pending.add(cutoff.isoformat())
                        continue
                    latest_date = date.fromisoformat(latest)
                    if latest_date >= cutoff:
                        continue
                    for day in _trading_days_between(latest_date, cutoff):
                        pending.add(day.isoformat())
                caught_up_to = max(
                    (value for value in latest_by_code.values() if value is not None),
                    default=None,
                )
                missed = tuple(sorted(pending))
            else:
                caught_up_to, missed = compute_labeler_gap(
                    ledger,
                    sectors=sectors,
                    as_of=target,
                    rule_hash=sector_lab.rule_hash,
                    label_window_days=label_window_days,
                )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"gap scan failed: {exc}")
        return LabelerCatchUpReport(
            as_of=target, caught_up_to=None, errors=tuple(errors)
        )
    if not missed:
        return LabelerCatchUpReport(
            as_of=target,
            caught_up_to=caught_up_to,
            errors=tuple(errors),
        )
    for missed_date in missed:
        if progress is not None:
            progress(f"补跑标注器 {missed_date}")
        try:
            sweep = run_nightly(
                source,
                trading_date=missed_date,
                sectors=sectors,
                ledger_database=ledger_database,
                shadow_database=shadow_database,
                production_directory=production_directory,
                report_directory=report_directory,
                confusion_database=confusion_database,
                behavior_database=behavior_database,
                llm_observations=llm_observations,
                capital_flow_database=capital_flow_database,
                watchlist=watchlist,
            )
            if sweep.errors:
                errors.extend(f"{missed_date}: {error}" for error in sweep.errors)
            else:
                ran += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{missed_date}: {exc}")
    return LabelerCatchUpReport(
        as_of=target,
        caught_up_to=caught_up_to,
        missed_dates=missed,
        ran_sweeps=ran,
        errors=tuple(errors),
    )


def _shift_trading_days(reference: date, offset: int) -> date:
    """Shift by calendar days, skipping weekends (K_DAY granularity)."""
    current = reference
    direction = 1 if offset >= 0 else -1
    steps = abs(offset)
    while steps:
        current += timedelta(days=direction)
        if current.weekday() < 5:
            steps -= 1
    return current


def _trading_days_between(lower: date, upper: date) -> tuple[date, ...]:
    result: list[date] = []
    current = lower + timedelta(days=1)
    while current <= upper:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return tuple(result)


def _bars_frame(bars: Sequence[Bar]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "turnover"])
    return pd.DataFrame(
        {
            "date": [bar.time_key for bar in bars],
            "open": [bar.open for bar in bars],
            "high": [bar.high for bar in bars],
            "low": [bar.low for bar in bars],
            "close": [bar.close for bar in bars],
            "volume": [bar.volume for bar in bars],
            "turnover": [bar.turnover for bar in bars],
        }
    )


def _capital_flows_reader(
    database: Path | str | None,
) -> Callable[[str], Sequence[CapitalFlow] | None]:
    """Return a ``code -> flows`` reader backed by the P0-8 ledger.

    A missing, empty or unreadable ledger yields ``None`` (the labeler then
    keeps its documented unavailable-flow behavior) and never aborts the
    sweep.
    """
    if database is None:
        return lambda code: None

    def read(code: str) -> Sequence[CapitalFlow] | None:
        try:
            with CapitalFlowLedger(database) as ledger:
                flows = ledger.flows_for(code)
        except Exception:  # noqa: BLE001 — capital flow must never abort labeling
            return None
        return flows if flows else None

    return read


def _iso(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    from src.data.capital_flow_daily import DEFAULT_CAPITAL_FLOW_DB

    parser = argparse.ArgumentParser(
        prog="secondordergame-labeler-nightly",
        description="晚间事后标注器调度：板块/个股标签落库 + v2 影子 + C 计数回灌",
    )
    parser.add_argument("--date", default=date.today().isoformat(), help="目标交易日 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--sectors", default="", help="板块代码列表，逗号分隔，如 BK0475,BK0476（板块模式；自选池模式忽略）")
    parser.add_argument("--sector-names", default="", help="板块显示名，逗号分隔，与 --sectors 顺序一致（可选）")
    parser.add_argument(
        "--pa-settings",
        default="",
        help="PA 二阶设置 settings.json 路径；提供后进入自选池模式（标注范围=自选池，基准=各标的关联板块）",
    )
    parser.add_argument(
        "--watchlist",
        default="",
        help="自选池代码，逗号分隔（自选池模式；缺省读 scope 文件 watchlist）",
    )
    parser.add_argument("--ledger-db", default=str(DEFAULT_LEDGER))
    parser.add_argument("--shadow-db", default=str(DEFAULT_SHADOW))
    parser.add_argument("--production-dir", default=str(DEFAULT_PRODUCTION))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORTS))
    parser.add_argument("--confusion-db", default=str(DEFAULT_CONFUSION))
    parser.add_argument("--no-confusion", action="store_true", help="跳过 C 计数回灌")
    parser.add_argument("--history-start", default=None, help="历史起点 YYYY-MM-DD（默认回看 400 天）")
    parser.add_argument(
        "--capital-flow-db",
        default=str(DEFAULT_CAPITAL_FLOW_DB),
        help=f"资金流台账路径（ROADMAP P0-8，默认 {DEFAULT_CAPITAL_FLOW_DB}），注入个股标注器参与者判定",
    )
    args = parser.parse_args(argv)

    sectors: dict[str, str] = {}
    watchlist: Mapping[str, Mapping[str, str]] | None = None
    if args.pa_settings:
        from src.data.capital_flow_daily import (
            DEFAULT_SCOPE_FILE,
            load_pa_settings_mapping,
            load_scope,
        )

        mapping = load_pa_settings_mapping(args.pa_settings)
        explicit = tuple(
            dict.fromkeys(item.strip() for item in args.watchlist.split(",") if item.strip())
        )
        if explicit:
            selected = explicit
        else:
            scope_watchlist, _ = load_scope(DEFAULT_SCOPE_FILE)
            selected = scope_watchlist
        watchlist = {code: mapping.get(code, {}) for code in selected}
        if not watchlist:
            parser.error("自选池为空（--watchlist 或 scope 文件）")
    else:
        codes = tuple(dict.fromkeys(item.strip() for item in args.sectors.split(",") if item.strip()))
        if not codes:
            parser.error("--sectors 不能为空（或使用 --pa-settings 进入自选池模式）")
        names = tuple(item.strip() for item in args.sector_names.split(",") if item.strip()) if args.sector_names else ()
        for index, code in enumerate(codes):
            sectors[code] = names[index] if index < len(names) else code

    # Production market source: Futu OpenD via the standard client.
    from src.data.futu_client import FutuMarketDataSource

    source = FutuMarketDataSource()
    try:
        report = run_nightly(
            source,
            trading_date=args.date,
            sectors=sectors,
            ledger_database=args.ledger_db,
            shadow_database=args.shadow_db,
            production_directory=args.production_dir,
            report_directory=args.report_dir,
            confusion_database=None if args.no_confusion else args.confusion_db,
            history_start=args.history_start,
            capital_flow_database=args.capital_flow_db,
            watchlist=watchlist,
        )
    finally:
        close = getattr(source, "close", None)
        if callable(close):
            close()

    _print_report(report)
    return 0 if not report.errors else 2


def _print_report(report: NightlyLabelingReport) -> None:
    print(f"[nightly] 交易日 {report.trading_date} 标签落库 {report.ledger_rows} 行")
    for run in report.sector_runs:
        print(
            f"  {run.sector_code} {run.sector_name}: 板块 {run.sector_labeled} / "
            f"个股 {run.stock_labeled} (unavailable {run.stock_unavailable})"
            + (f" / v2 {run.v2_status}" if run.v2_status else "")
        )
    print(f"[nightly] v2 切流: {report.cutover_status} — {report.cutover_reason}")
    if report.confusion_increments:
        print(f"[nightly] C 计数回灌: {sum(report.confusion_increments.values())} 次增量")
    for error in report.errors:
        print(f"[nightly] ERROR: {error}")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CATCHUP_LABEL_WINDOW",
    "LabelerCatchUpReport",
    "NightlyLabelingReport",
    "SectorRunReport",
    "compute_labeler_gap",
    "run_labeler_catchup",
    "run_nightly",
]
