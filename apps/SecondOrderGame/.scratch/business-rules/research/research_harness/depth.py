from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

from .errors import HistoryProviderError
from .models import HistoryRequest, Period, ResearchConfig
from .provider import HistoryProvider


@dataclass(frozen=True)
class HistoryDepthEntry:
    code: str
    kind: str
    period: Period
    requested_start: date
    requested_end: date
    earliest: date | None
    latest: date | None
    row_count: int
    trading_day_count: int
    elapsed_seconds: float
    page_count: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "kind": self.kind,
            "period": self.period,
            "requested_range": {
                "start": self.requested_start.isoformat(),
                "end": self.requested_end.isoformat(),
            },
            "earliest": self.earliest.isoformat() if self.earliest else None,
            "latest": self.latest.isoformat() if self.latest else None,
            "first_available_time": self.earliest.isoformat() if self.earliest else None,
            "last_available_time": self.latest.isoformat() if self.latest else None,
            "row_count": self.row_count,
            "trading_day_count": self.trading_day_count,
            "elapsed_seconds": self.elapsed_seconds,
            "page_count": self.page_count,
            "error": self.error,
            "interface_error": self.error,
            "ktype": self.period,
        }


@dataclass(frozen=True)
class HistoryDepthReport:
    entries: tuple[HistoryDepthEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [entry.to_dict() for entry in self.entries]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def to_markdown(self) -> str:
        lines = [
            "# 富途历史深度测量",
            "",
            "| 标的 | 类型 | 周期 | 最早日 | 最新日 | 原始行数 | 交易日数 | 页数 | 用时（秒） | 错误 |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for entry in self.entries:
            earliest = entry.earliest.isoformat() if entry.earliest else "无数据"
            latest = entry.latest.isoformat() if entry.latest else "无数据"
            lines.append(
                f"| {entry.code} | {entry.kind} | {entry.period} | {earliest} | {latest} | "
                f"{entry.row_count} | {entry.trading_day_count} | {entry.page_count if entry.page_count is not None else '未知'} | "
                f"{entry.elapsed_seconds:.3f} | {entry.error or ''} |"
            )
        return "\n".join(lines) + "\n"


def measure_history_depth(
    config: ResearchConfig,
    provider: HistoryProvider,
    *,
    periods: Sequence[Period] = ("day", "120m"),
    start: date | None = None,
    end: date | None = None,
    clock: Callable[[], float] | None = None,
) -> HistoryDepthReport:
    """Measure the oldest/newest rows returned for every configured instrument/period."""
    selected_periods = _normalise_periods(periods)
    requested_start = start or config.data.start
    requested_end = end or config.data.end
    if requested_start > requested_end:
        raise ValueError("start must not be after end")
    now = clock or perf_counter
    entries: list[HistoryDepthEntry] = []
    for instrument in config.data.instruments:
        for period in selected_periods:
            request = HistoryRequest(
                code=instrument.code,
                kind=instrument.kind,
                period=period,
                start=requested_start,
                end=requested_end,
            )
            started = now()
            error: str | None = None
            try:
                rows = tuple(provider.fetch_history(request))
            except Exception as exc:
                error = str(exc)
                rows = ()
            elapsed = round(max(0.0, now() - started), 3)
            dates = []
            for index, row in enumerate(rows):
                if not isinstance(row, Mapping):
                    raise HistoryProviderError(
                        f"history depth row {instrument.code}[{index}] must be a mapping"
                    )
                trading_date = _row_date(row, instrument.code, index)
                if requested_start <= trading_date <= requested_end:
                    dates.append(trading_date)
            entries.append(
                HistoryDepthEntry(
                    code=instrument.code,
                    kind=instrument.kind,
                    period=period,
                    requested_start=requested_start,
                    requested_end=requested_end,
                    earliest=min(dates) if dates else None,
                    latest=max(dates) if dates else None,
                    row_count=len(dates),
                    trading_day_count=len(set(dates)),
                    elapsed_seconds=elapsed,
                    page_count=_provider_page_count(provider),
                    error=error,
                )
            )
    return HistoryDepthReport(entries=tuple(entries))


def _provider_page_count(provider: HistoryProvider) -> int | None:
    """Read optional pagination metadata without expanding the provider protocol."""

    value = getattr(provider, "last_page_count", None)
    return value if type(value) is int and value >= 0 else None


def _normalise_periods(periods: Sequence[Period]) -> tuple[Period, ...]:
    aliases = {"day": "day", "k_day": "day", "120m": "120m", "k_120m": "120m"}
    output: list[Period] = []
    for period in periods:
        if period not in aliases:
            raise ValueError(f"unsupported history period: {period!r}")
        canonical = aliases[period]
        if canonical not in output:
            output.append(canonical)
    if not output:
        raise ValueError("periods must not be empty")
    return tuple(output)


def _row_date(row: Mapping[str, Any], code: str, index: int) -> date:
    value = row.get("trading_date", row.get("time_key", row.get("timestamp")))
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise HistoryProviderError(
                f"history depth row {code}[{index}] has invalid trading date {value!r}"
            ) from exc
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime().date()
    if hasattr(value, "date"):
        converted = value.date()
        if isinstance(converted, date):
            return converted
    raise HistoryProviderError(f"history depth row {code}[{index}] has no trading date")
