from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from .errors import RuleEvaluationError
from .models import HistoryRequest, ResearchConfig
from .provider import HistoryProvider


@dataclass(frozen=True)
class DayMatch:
    code: str
    trading_date: date
    labels: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "trading_date": self.trading_date.isoformat(),
            "labels": list(self.labels),
        }


@dataclass(frozen=True)
class LabelStat:
    label: str
    count: int
    share: float

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "count": self.count, "share": self.share}


@dataclass(frozen=True)
class ReplayReport:
    config_version: int
    period: str
    requested_start: date
    requested_end: date
    total_rows: int
    total_code_days: int
    label_stats: tuple[LabelStat, ...]
    multi_label_conflict_count: int
    multi_label_conflict_share: float
    conflict_combinations: Mapping[str, int]
    unmatched_count: int
    unmatched_share: float
    matches: tuple[DayMatch, ...]

    @property
    def label_counts(self) -> dict[str, int]:
        return {stat.label: stat.count for stat in self.label_stats}

    @property
    def label_shares(self) -> dict[str, float]:
        return {stat.label: stat.share for stat in self.label_stats}

    def to_dict(self, include_matches: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "config_version": self.config_version,
            "period": self.period,
            "requested_range": {
                "start": self.requested_start.isoformat(),
                "end": self.requested_end.isoformat(),
            },
            "total_rows": self.total_rows,
            "total_code_days": self.total_code_days,
            "label_stats": [stat.to_dict() for stat in self.label_stats],
            "multi_label_conflicts": {
                "count": self.multi_label_conflict_count,
                "share": self.multi_label_conflict_share,
                "combinations": dict(self.conflict_combinations),
            },
            "unmatched": {
                "count": self.unmatched_count,
                "share": self.unmatched_share,
            },
        }
        if include_matches:
            payload["matches"] = [match.to_dict() for match in self.matches]
        return payload

    def to_json(self, include_matches: bool = False) -> str:
        return json.dumps(
            self.to_dict(include_matches=include_matches),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"

    def to_markdown(self, include_matches: bool = False) -> str:
        lines = [
            "# 标注规则回放报告",
            "",
            f"- 配置版本：`{self.config_version}`",
            f"- 周期：`{self.period}`",
            f"- 请求区间：`{self.requested_start.isoformat()}` 至 `{self.requested_end.isoformat()}`",
            f"- 原始行数：`{self.total_rows}`",
            f"- 统计分母：{self.total_code_days} 个「标的×交易日」",
            "",
            "## 标签分布",
            "",
            "| 标签 | 命中次数 | 占比 |",
            "| --- | ---: | ---: |",
        ]
        for stat in self.label_stats:
            lines.append(f"| {stat.label} | {stat.count} | {stat.share:.2%} |")
        lines.extend(
            [
                "",
                "## 冲突与未命中",
                "",
                f"- 多标签日：`{self.multi_label_conflict_count}` （`{self.multi_label_conflict_share:.2%}`）",
                f"- 未命中日：`{self.unmatched_count}` （`{self.unmatched_share:.2%}`）",
            ]
        )
        if self.conflict_combinations:
            lines.extend(["", "冲突组合："])
            for combination, count in self.conflict_combinations.items():
                lines.append(f"- `{combination}`：`{count}` 次")
        if include_matches:
            lines.extend(["", "## 标的×交易日明细", ""])
            lines.extend(["| 标的 | 交易日 | 命中标签 |", "| --- | --- | --- |"])
            for match in self.matches:
                labels = "、".join(match.labels) if match.labels else "无标签"
                lines.append(f"| {match.code} | {match.trading_date.isoformat()} | {labels} |")
        return "\n".join(lines) + "\n"


def replay(config: ResearchConfig, provider: HistoryProvider) -> ReplayReport:
    """Fetch configured history and aggregate rule matches at code-trading-day level."""
    label_order = tuple(rule.label for rule in config.rules)
    order_index = {label: index for index, label in enumerate(label_order)}
    labels_by_day: dict[tuple[str, date], set[str]] = defaultdict(set)
    total_rows = 0

    for instrument in config.data.instruments:
        request = HistoryRequest(
            code=instrument.code,
            kind=instrument.kind,
            period=config.data.period,
            start=config.data.start,
            end=config.data.end,
        )
        rows = provider.fetch_history(request)
        for row_index, raw_row in enumerate(rows):
            if not isinstance(raw_row, Mapping):
                raise RuleEvaluationError(
                    f"provider row {instrument.code}[{row_index}] must be a mapping"
                )
            record = _normalise_row(raw_row, request)
            trading_date = record["trading_date"]
            if not config.data.start <= trading_date <= config.data.end:
                continue
            total_rows += 1
            key = (record["code"], trading_date)
            day_labels = labels_by_day[key]
            for rule in config.rules:
                try:
                    if rule.matches(record):
                        day_labels.add(rule.label)
                except RuleEvaluationError as exc:
                    raise RuleEvaluationError(
                        f"{exc} (at {record['code']} {trading_date.isoformat()})"
                    ) from exc

    matches: list[DayMatch] = []
    label_counts = Counter({label: 0 for label in label_order})
    conflict_counts: Counter[str] = Counter()
    unmatched_count = 0
    for code, trading_date in sorted(labels_by_day):
        labels = tuple(sorted(labels_by_day[(code, trading_date)], key=order_index.__getitem__))
        matches.append(DayMatch(code=code, trading_date=trading_date, labels=labels))
        label_counts.update(labels)
        if not labels:
            unmatched_count += 1
        if len(labels) >= 2:
            conflict_counts[" + ".join(labels)] += 1

    total_code_days = len(matches)
    conflict_count = sum(1 for match in matches if len(match.labels) >= 2)
    denominator = total_code_days or 1
    label_stats = tuple(
        LabelStat(label=label, count=label_counts[label], share=label_counts[label] / denominator)
        for label in label_order
    )
    return ReplayReport(
        config_version=config.version,
        period=config.data.period,
        requested_start=config.data.start,
        requested_end=config.data.end,
        total_rows=total_rows,
        total_code_days=total_code_days,
        label_stats=label_stats,
        multi_label_conflict_count=conflict_count,
        multi_label_conflict_share=conflict_count / denominator if total_code_days else 0.0,
        conflict_combinations=dict(sorted(conflict_counts.items())),
        unmatched_count=unmatched_count,
        unmatched_share=unmatched_count / denominator if total_code_days else 0.0,
        matches=tuple(matches),
    )


def _normalise_row(row: Mapping[str, Any], request: HistoryRequest) -> dict[str, Any]:
    record = {str(key): _json_scalar(value) for key, value in row.items()}
    code = record.get("code", request.code)
    if not isinstance(code, str) or not code:
        raise RuleEvaluationError(f"provider row for {request.code} has invalid code")
    if code != request.code:
        raise RuleEvaluationError(
            f"provider row code {code!r} does not match requested code {request.code!r}"
        )
    raw_date = record.get("trading_date")
    if raw_date is None:
        raw_date = record.get("time_key", record.get("timestamp"))
    trading_date = _parse_row_date(raw_date)
    record.update(
        {
            "code": code,
            "trading_date": trading_date,
            "period": request.period,
            "instrument_kind": request.kind,
        }
    )
    return record


def _parse_row_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise RuleEvaluationError(f"invalid provider trading date: {value!r}") from exc
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime().date()
    if hasattr(value, "date"):
        converted = value.date()
        if isinstance(converted, date):
            return converted
    raise RuleEvaluationError(f"provider row has no parseable trading date: {value!r}")


def _json_scalar(value: Any) -> Any:
    """Convert common pandas/numpy scalar values without importing either package."""
    if hasattr(value, "item") and callable(value.item):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value
