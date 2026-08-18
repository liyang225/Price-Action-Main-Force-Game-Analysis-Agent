"""Per-symbol historical analysis archive."""
from __future__ import annotations

import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pa_agent.ai.cycle_enums import format_cycle_with_direction, format_trend_label
from pa_agent.config.paths import HISTORY_ANALYSIS_DIR
from pa_agent.data.datetime_ts import ts_open_to_ms
from pa_agent.records.schema import AnalysisRecord
from pa_agent.records.trade_rules import (
    ManualEntryOverride,
    SETTLEMENT_T0,
    SETTLEMENT_T1,
    SETTLEMENT_UNSET,
    normalize_settlement_mode,
)


_UNSAFE_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_NO_ORDER_TOKENS = ("不下单", "wait", "等待", "none", "no_order")


@dataclass(frozen=True)
class HistoryAnalysisEntry:
    """One saved historical analysis record."""

    path: Path
    symbol: str
    timeframe: str
    timestamp_ms: int
    timestamp_iso: str
    decision_label: str
    current_price: float | None
    price_change: float | None
    price_change_pct: float | None
    entry_price: float | None
    take_profit_price: float | None
    stop_loss_price: float | None
    trend_label: str
    trend_direction: str | None
    cycle_label: str
    resistance_levels: tuple[str, ...]
    support_levels: tuple[str, ...]
    row_kind: str = "analysis"
    trade_event: "HistoryTradeEvent | None" = None


@dataclass(frozen=True)
class TradeTouch:
    """One target/stop touch that could not yet close a T+1 plan."""

    outcome: str
    timestamp_ms: int
    price: float


@dataclass(frozen=True)
class HistoryTradeEvent:
    """A system-detected outcome belonging to an earlier AI trade plan."""

    source_path: Path
    source_timestamp_ms: int
    source_direction: str
    settlement_mode: str
    entry_price: float
    take_profit_price: float
    stop_loss_price: float
    entry_timestamp_ms: int
    timestamp_ms: int
    outcome: str | None
    exit_price: float | None
    return_pct: float | None
    blocked_touches: tuple[TradeTouch, ...] = ()


def safe_stock_folder_name(symbol: str) -> str:
    """Return a filesystem-safe folder name for one stock symbol."""
    cleaned = _UNSAFE_PATH_CHARS.sub("_", str(symbol or "").strip())
    cleaned = cleaned.strip(" ._")
    return cleaned or "unknown"


def stock_history_dir(symbol: str, root: Path | None = None) -> Path:
    return (root or HISTORY_ANALYSIS_DIR) / safe_stock_folder_name(symbol)


def _record_filename(record: AnalysisRecord) -> str:
    meta = record.meta
    ts = str(meta.timestamp_local_iso or str(meta.timestamp_local_ms))
    ts = ts.replace(":", "-").replace("T", "_").replace("+", "_")
    ts = _UNSAFE_PATH_CHARS.sub("_", ts).strip(" ._")
    symbol = safe_stock_folder_name(meta.symbol)
    timeframe = safe_stock_folder_name(meta.timeframe)
    return f"{ts}_{symbol}_{timeframe}.json"


def save_history_record(record: AnalysisRecord, root: Path | None = None) -> Path | None:
    """Save a complete decision record into its symbol-specific history folder."""
    if record.exception is not None:
        return None
    if not record.stage2_decision:
        return None
    target_dir = stock_history_dir(record.meta.symbol, root)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / _record_filename(record)
    data = record.model_dump()
    text = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    return path


def copy_history_record(source_path: Path, record: AnalysisRecord, root: Path | None = None) -> Path | None:
    """Copy an already-saved complete record into the history archive."""
    if record.exception is not None or not record.stage2_decision:
        return None
    target_dir = stock_history_dir(record.meta.symbol, root)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source_path.name
    if source_path.resolve() == target.resolve():
        return target
    shutil.copy2(source_path, target)
    return target


def load_history_record(path: Path) -> AnalysisRecord | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("_partial_reason", None)
        return AnalysisRecord.model_validate(raw)
    except Exception:
        return None


def _decision_dict(record: AnalysisRecord) -> dict:
    stage2 = record.stage2_decision if isinstance(record.stage2_decision, dict) else {}
    decision = stage2.get("decision") if isinstance(stage2, dict) else {}
    return decision if isinstance(decision, dict) else {}


def _stage1_or_stage2_direction(record: AnalysisRecord) -> str | None:
    stage2 = record.stage2_decision if isinstance(record.stage2_decision, dict) else {}
    diagnosis_summary = stage2.get("diagnosis_summary") if isinstance(stage2, dict) else None
    if isinstance(diagnosis_summary, dict):
        direction = str(diagnosis_summary.get("direction") or "").strip()
        if direction:
            return direction
    stage1 = record.stage1_diagnosis if isinstance(record.stage1_diagnosis, dict) else {}
    direction = str(stage1.get("direction") or "").strip() if isinstance(stage1, dict) else ""
    return direction or None


def _diagnosis_value(record: AnalysisRecord, key: str) -> object:
    stage2 = record.stage2_decision if isinstance(record.stage2_decision, dict) else {}
    summary = stage2.get("diagnosis_summary") if isinstance(stage2, dict) else None
    if isinstance(summary, dict) and summary.get(key) not in (None, "", []):
        return summary[key]
    stage1 = record.stage1_diagnosis if isinstance(record.stage1_diagnosis, dict) else {}
    return stage1.get(key) if isinstance(stage1, dict) else None


def _summary_level_values(record: AnalysisRecord) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the support/resistance texts shown in the analysis summary row."""
    from pa_agent.gui.support_resistance import nearest_support_resistance_labels

    stage1 = record.stage1_diagnosis if isinstance(record.stage1_diagnosis, dict) else {}
    support, resistance = nearest_support_resistance_labels(stage1)
    supports = () if support in ("", "—", "-") else (support,)
    resistances = () if resistance in ("", "—", "-") else (resistance,)
    return supports, resistances


def _trend_direction(direction: str | None) -> str | None:
    if direction in ("bullish", "up"):
        return "bullish"
    if direction in ("bearish", "down"):
        return "bearish"
    if direction == "neutral":
        return "neutral"
    return None


def _is_long_direction(direction: object) -> bool | None:
    text = str(direction or "").strip().lower()
    if "多" in text or text in ("long", "buy", "bull", "bullish"):
        return True
    if "空" in text or text in ("short", "sell", "bear", "bearish"):
        return False
    return None


def _decision_label(record: AnalysisRecord) -> str:
    decision = _decision_dict(record)
    order_type = str(decision.get("order_type") or "").strip()
    lower = order_type.lower()
    if not order_type:
        return "-"
    if any(token in order_type for token in _NO_ORDER_TOKENS) or lower in _NO_ORDER_TOKENS:
        return "不下单"

    order_direction = str(decision.get("order_direction") or "").strip()
    if order_direction:
        long = _is_long_direction(order_direction)
        if long is True:
            return "做多单"
        if long is False:
            return "做空单"
    if "buy" in lower or "多" in order_type:
        return "做多单"
    if "sell" in lower or "空" in order_type:
        return "做空单"
    return order_type


def _current_price(record: AnalysisRecord) -> float | None:
    if not record.kline_data:
        return None
    bar = record.kline_data[0]
    try:
        return float(bar.get("close"))
    except (TypeError, ValueError, AttributeError):
        return None


def _price_value(raw: object) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _entry_price(record: AnalysisRecord) -> float | None:
    return _price_value(_decision_dict(record).get("entry_price"))


def _take_profit_price(record: AnalysisRecord) -> float | None:
    return _price_value(_decision_dict(record).get("take_profit_price"))


def _stop_loss_price(record: AnalysisRecord) -> float | None:
    return _price_value(_decision_dict(record).get("stop_loss_price"))


def _has_trade(record: AnalysisRecord) -> bool:
    order_type = str(_decision_dict(record).get("order_type") or "").strip().lower()
    return bool(order_type and not any(token in order_type for token in _NO_ORDER_TOKENS))


def _bar_timestamp_ms(bar: dict) -> int | None:
    try:
        return int(ts_open_to_ms(float(bar.get("ts_open"))))
    except (TypeError, ValueError, AttributeError):
        return None


def _bars_after_plan(
    plan_record: AnalysisRecord,
    later_records: list[AnalysisRecord],
) -> list[dict]:
    """Return de-duplicated closed bars after a plan's latest analysed K line."""
    if not plan_record.kline_data:
        return []
    anchor = _bar_timestamp_ms(plan_record.kline_data[0])
    if anchor is None:
        return []

    bars_by_timestamp: dict[int, dict] = {}
    for record in later_records:
        for bar in record.kline_data:
            timestamp_ms = _bar_timestamp_ms(bar)
            if timestamp_ms is not None and timestamp_ms > anchor and bool(bar.get("closed", True)):
                bars_by_timestamp[timestamp_ms] = bar
    return [bars_by_timestamp[key] for key in sorted(bars_by_timestamp)]


def _entry_is_triggered(
    bar: dict,
    *,
    long: bool,
    order_type: object,
    entry: float,
    tolerance: float = 0.0,
) -> bool:
    """Whether the planned order could have entered on this closed OHLC bar."""
    try:
        high = float(bar.get("high"))
        low = float(bar.get("low"))
    except (TypeError, ValueError, AttributeError):
        return False

    text = str(order_type or "").strip().lower()
    if "市价" in text or text in {"market", "buy", "sell"}:
        return True
    if "限价" in text or "limit" in text:
        return low <= entry + tolerance if long else high >= entry - tolerance
    # Breakout/stop-entry orders need the direction's extreme to cross the entry.
    return high >= entry if long else low <= entry


def _flexible_limit_fill_price(bar: dict, *, long: bool, entry: float) -> float:
    if long:
        low = _price_value(bar.get("low"))
        return max(entry, low) if low is not None else entry
    high = _price_value(bar.get("high"))
    return min(entry, high) if high is not None else entry


def _is_market_order(order_type: object) -> bool:
    """Whether a plan enters immediately when the analysis is published."""
    text = str(order_type or "").strip().lower()
    return "市价" in text or text in {"market", "buy", "sell"}


def _bar_exit_outcome(
    bar: dict,
    *,
    long: bool,
    take_profit: float,
    stop_loss: float,
) -> str | None:
    """Return a target touch or close-confirmed stop outcome."""
    try:
        high = float(bar.get("high"))
        low = float(bar.get("low"))
    except (TypeError, ValueError, AttributeError):
        return None
    close_price = _bar_close(bar)
    hit_tp = high >= take_profit if long else low <= take_profit
    hit_sl = (
        close_price is not None
        and (close_price <= stop_loss if long else close_price >= stop_loss)
    )
    if hit_tp and hit_sl:
        open_price = _price_value(bar.get("open"))
        if open_price is None or close_price is None:
            return "sl"
        if close_price > open_price:
            return "tp" if long else "sl"
        if close_price < open_price:
            return "sl" if long else "tp"
        return (
            "tp"
            if abs(take_profit - open_price) < abs(stop_loss - open_price)
            else "sl"
        )
    if hit_tp:
        return "tp"
    if hit_sl:
        return "sl"
    return None


def _bar_close(bar: dict) -> float | None:
    return _price_value(bar.get("close"))


def _bar_hits_stop(bar: dict, *, long: bool, stop_loss: float) -> bool:
    close_price = _bar_close(bar)
    if close_price is None:
        return False
    return close_price <= stop_loss if long else close_price >= stop_loss


def _infer_price_tick(records: list[AnalysisRecord]) -> float:
    max_decimals = 0
    for record in records:
        for bar in record.kline_data:
            for field in ("open", "high", "low", "close"):
                value = _price_value(bar.get(field))
                if value is None:
                    continue
                text = f"{value:.12f}".rstrip("0").rstrip(".")
                if "." in text:
                    max_decimals = max(max_decimals, len(text.split(".")[1]))
    return 10 ** (-min(max_decimals, 6)) if max_decimals else 0.0


def _manual_entry_for_path(
    source_path: Path,
    entry_overrides: Mapping[str, ManualEntryOverride] | None,
) -> ManualEntryOverride | None:
    if entry_overrides is None:
        return None
    return entry_overrides.get(str(source_path.resolve())) or entry_overrides.get(str(source_path))


def _record_timezone(record: AnalysisRecord):
    """Read the market-local offset captured with the archived analysis."""
    try:
        timestamp = str(record.meta.timestamp_local_iso or "").replace("Z", "+00:00")
        tz = datetime.fromisoformat(timestamp).tzinfo
        if tz is not None:
            return tz
    except (TypeError, ValueError):
        pass
    return timezone.utc


def _trading_day(timestamp_ms: int, *, tz) -> object:
    """Use the archived market-local day, never the host or archive-read day."""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=tz).date()


def _return_pct(*, entry: float, exit_price: float, long: bool) -> float | None:
    if entry == 0:
        return None
    movement = exit_price - entry if long else entry - exit_price
    return round(movement / entry * 100.0, 10)


def _evaluate_trade_plan(
    *,
    source_path: Path,
    source_record: AnalysisRecord,
    later_records: list[AnalysisRecord],
    settlement_mode: str,
    entry_tolerance_ticks: int = 0,
    manual_entry: ManualEntryOverride | None = None,
) -> HistoryTradeEvent | None:
    """Evaluate one archived plan without attributing it to a later AI analysis row."""
    if not _has_trade(source_record):
        return None
    mode = normalize_settlement_mode(settlement_mode)
    if mode == SETTLEMENT_UNSET:
        return None

    planned_entry = _entry_price(source_record)
    take_profit = _take_profit_price(source_record)
    stop_loss = _stop_loss_price(source_record)
    direction = _decision_dict(source_record).get("order_direction")
    long = _is_long_direction(direction)
    if planned_entry is None or take_profit is None or stop_loss is None or long is None:
        return None
    entry = manual_entry.price if manual_entry is not None else planned_entry
    order_type = _decision_dict(source_record).get("order_type")

    entry_timestamp_ms: int | None = (
        int(manual_entry.timestamp_ms)
        if manual_entry is not None
        else (
            int(source_record.meta.timestamp_local_ms)
            if _is_market_order(_decision_dict(source_record).get("order_type"))
            else None
        )
    )
    entry_tolerance = (
        _infer_price_tick([source_record, *later_records])
        * max(0, int(entry_tolerance_ticks))
        if manual_entry is None
        else 0.0
    )
    blocked_touches: list[TradeTouch] = []
    market_timezone = _record_timezone(source_record)

    def build_event(
        *,
        timestamp_ms: int,
        outcome: str | None,
        exit_price: float | None = None,
    ) -> HistoryTradeEvent:
        assert entry_timestamp_ms is not None
        return HistoryTradeEvent(
            source_path=source_path,
            source_timestamp_ms=int(source_record.meta.timestamp_local_ms),
            source_direction="做多" if long else "做空",
            settlement_mode=mode,
            entry_price=entry,
            take_profit_price=take_profit,
            stop_loss_price=stop_loss,
            entry_timestamp_ms=entry_timestamp_ms,
            timestamp_ms=timestamp_ms,
            outcome=outcome,
            exit_price=exit_price,
            return_pct=(
                _return_pct(entry=entry, exit_price=exit_price, long=long)
                if exit_price is not None
                else None
            ),
            blocked_touches=tuple(blocked_touches),
        )

    for bar in _bars_after_plan(source_record, later_records):
        timestamp_ms = _bar_timestamp_ms(bar)
        if timestamp_ms is None:
            continue
        if manual_entry is not None and timestamp_ms < entry_timestamp_ms:
            continue
        if entry_timestamp_ms is None:
            if _entry_is_triggered(
                bar,
                long=long,
                order_type=order_type,
                entry=planned_entry,
                tolerance=entry_tolerance,
            ):
                if _bar_hits_stop(bar, long=long, stop_loss=stop_loss):
                    return None
                if entry_tolerance and ("限价" in str(order_type) or "limit" in str(order_type).lower()):
                    entry = _flexible_limit_fill_price(bar, long=long, entry=planned_entry)
                entry_timestamp_ms = timestamp_ms
            else:
                continue

        outcome = _bar_exit_outcome(
            bar,
            long=long,
            take_profit=take_profit,
            stop_loss=stop_loss,
        )
        if outcome is None:
            continue

        touch_price = take_profit if outcome == "tp" else stop_loss
        if mode == SETTLEMENT_T1 and _trading_day(
            timestamp_ms,
            tz=market_timezone,
        ) == _trading_day(entry_timestamp_ms, tz=market_timezone):
            blocked_touches.append(
                TradeTouch(outcome=outcome, timestamp_ms=timestamp_ms, price=touch_price)
            )
            continue
        exit_price = _bar_close(bar)
        if exit_price is None:
            continue
        return build_event(
            timestamp_ms=timestamp_ms,
            outcome=outcome,
            exit_price=exit_price,
        )

    if entry_timestamp_ms is not None and blocked_touches:
        last_touch = blocked_touches[-1]
        return build_event(timestamp_ms=last_touch.timestamp_ms, outcome=None)
    return None


def list_history_entries(
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    settlement_mode: str = SETTLEMENT_UNSET,
    entry_tolerance_ticks: int = 0,
    entry_overrides: Mapping[str, ManualEntryOverride] | None = None,
    root: Path | None = None,
) -> list[HistoryAnalysisEntry]:
    """List analysis records and explicit derived trade events, newest first."""
    base = root or HISTORY_ANALYSIS_DIR
    if not base.is_dir():
        return []
    dirs = [stock_history_dir(symbol, base)] if symbol else [p for p in base.iterdir() if p.is_dir()]

    records: list[tuple[Path, AnalysisRecord]] = []
    for folder in dirs:
        if not folder.is_dir():
            continue
        for path in folder.glob("*.json"):
            if not path.is_file():
                continue
            record = load_history_record(path)
            if record is None or record.exception is not None or not record.stage2_decision:
                continue
            meta = record.meta
            if symbol and meta.symbol != symbol:
                continue
            records.append((path, record))

    records.sort(key=lambda item: int(item[1].meta.timestamp_local_ms))

    previous_by_symbol: dict[str, AnalysisRecord] = {}
    records_by_market: dict[tuple[str, str], list[tuple[Path, AnalysisRecord]]] = {}

    analysis_entries: list[HistoryAnalysisEntry] = []
    displayed_paths: set[Path] = set()
    for path, record in records:
        market_key = (record.meta.symbol, record.meta.timeframe)
        prev_record = previous_by_symbol.get(record.meta.symbol)
        current_price = _current_price(record)
        price_change = None
        price_change_pct = None
        if prev_record is not None:
            prev_price = _current_price(prev_record)
            if current_price is not None and prev_price not in (None, 0):
                try:
                    price_change = current_price - float(prev_price)
                    price_change_pct = price_change / float(prev_price) * 100.0
                except (TypeError, ValueError):
                    price_change = None
                    price_change_pct = None

        meta = record.meta
        direction = _stage1_or_stage2_direction(record)
        cycle_position = str(_diagnosis_value(record, "cycle_position") or "")
        support_values, resistance_values = _summary_level_values(record)
        has_trade = _has_trade(record)
        ts = int(meta.timestamp_local_ms)
        in_timeframe = timeframe is None or meta.timeframe == timeframe
        in_date_range = (start_ms is None or ts >= start_ms) and (end_ms is None or ts <= end_ms)
        if in_timeframe and in_date_range:
            analysis_entries.append(
                HistoryAnalysisEntry(
                    path=path,
                    symbol=meta.symbol,
                    timeframe=meta.timeframe,
                    timestamp_ms=ts,
                    timestamp_iso=meta.timestamp_local_iso,
                    decision_label=_decision_label(record),
                    current_price=current_price,
                    price_change=price_change,
                    price_change_pct=price_change_pct,
                    entry_price=_entry_price(record) if has_trade else None,
                    take_profit_price=_take_profit_price(record) if has_trade else None,
                    stop_loss_price=_stop_loss_price(record) if has_trade else None,
                    trend_label=format_trend_label(direction, cycle_position),
                    trend_direction=_trend_direction(direction),
                    cycle_label=format_cycle_with_direction(cycle_position, direction),
                    resistance_levels=resistance_values,
                    support_levels=support_values,
                )
            )
            displayed_paths.add(path)
        previous_by_symbol[record.meta.symbol] = record
        records_by_market.setdefault(market_key, []).append((path, record))

    events_by_source: dict[Path, list[HistoryAnalysisEntry]] = {}
    mode = normalize_settlement_mode(settlement_mode)
    if mode != SETTLEMENT_UNSET:
        for timeline in records_by_market.values():
            for index, (source_path, source_record) in enumerate(timeline[:-1]):
                if source_path not in displayed_paths:
                    continue
                event = _evaluate_trade_plan(
                    source_path=source_path,
                    source_record=source_record,
                    later_records=[record for _path, record in timeline[index + 1 :]],
                    settlement_mode=mode,
                    entry_tolerance_ticks=entry_tolerance_ticks,
                    manual_entry=_manual_entry_for_path(source_path, entry_overrides),
                )
                if event is None:
                    continue
                meta = source_record.meta
                events_by_source.setdefault(source_path, []).append(
                    HistoryAnalysisEntry(
                        path=source_path,
                        symbol=meta.symbol,
                        timeframe=meta.timeframe,
                        timestamp_ms=event.timestamp_ms,
                        timestamp_iso=meta.timestamp_local_iso,
                        decision_label="盈损",
                        current_price=None,
                        price_change=None,
                        price_change_pct=None,
                        entry_price=None,
                        take_profit_price=None,
                        stop_loss_price=None,
                        trend_label="",
                        trend_direction=None,
                        cycle_label="",
                        resistance_levels=(),
                        support_levels=(),
                        row_kind="trade_event",
                        trade_event=event,
                    )
                )

    entries: list[HistoryAnalysisEntry] = []
    for analysis_entry in sorted(analysis_entries, key=lambda entry: entry.timestamp_ms, reverse=True):
        events = events_by_source.get(analysis_entry.path, [])
        entries.extend(sorted(events, key=lambda entry: entry.timestamp_ms, reverse=True))
        entries.append(analysis_entry)
    return entries


def count_history_records(symbol: str, root: Path | None = None) -> int:
    return sum(
        entry.row_kind == "analysis"
        for entry in list_history_entries(symbol=symbol, root=root)
    )
