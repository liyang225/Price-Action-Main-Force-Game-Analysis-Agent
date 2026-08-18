"""Independent sector labeler v2 using Futu membership and stock breadth."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

import yaml


_DEFAULT_CONFIG = Path(__file__).parents[2] / "config" / "sector_labeler_v2.yaml"


class TrendState(str, Enum):
    LOW_DORMANT = "low_dormant"
    LOW_TURNING = "low_turning"
    UP_CONFIRMED = "up_confirmed"
    HIGH_ACCELERATING = "high_accelerating"
    HIGH_REVERSING = "high_reversing"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class ConstituentDailyObservation:
    code: str
    limit_streak: int
    is_rise_limit: bool
    is_fall_limit: bool
    volume: float
    previous_five_day_average_volume: float

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("code must be non-empty")
        if isinstance(self.limit_streak, bool) or not isinstance(self.limit_streak, int) or self.limit_streak < 0:
            raise ValueError("limit_streak must be a non-negative integer")
        if self.is_rise_limit and self.is_fall_limit:
            raise ValueError("one stock cannot be both rise-limit and fall-limit")
        for name in ("volume", "previous_five_day_average_volume"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")


@dataclass(frozen=True, slots=True)
class SectorV2Metrics:
    constituent_count: int
    highest_limit_streak: int | None
    highest_streak_stock_count: int | None
    highest_streak_constituent_ratio: float | None
    rise_limit_count: int
    fall_limit_count: int
    limit_balance: float | None
    limit_activity: float | None
    relative_volume: float | None


@dataclass(frozen=True, slots=True)
class SectorV2Label:
    sector_code: str
    trading_date: str
    status: str
    metrics: SectorV2Metrics
    cycle_position: str | None
    consensus_state: str | None
    consensus_direction: str | None
    cycle_event: str | None
    rule_hash: str
    config_version: int
    reason: str | None = None


class SectorLabelerV2:
    def __init__(self, config_path: Path | str = _DEFAULT_CONFIG) -> None:
        self._path = Path(config_path)
        self._config = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        _validate_config(self._config)
        self.version = self._config["version"]
        self.rule_hash = hashlib.sha256(self._path.read_bytes()).hexdigest()

    def label_day(
        self,
        *,
        sector_code: str,
        trading_date: str,
        futu_constituent_codes: Iterable[str],
        akshare_observations: Iterable[ConstituentDailyObservation],
        trend_state: TrendState,
    ) -> SectorV2Label:
        codes = tuple(dict.fromkeys(_normalize_code(code) for code in futu_constituent_codes))
        observations = tuple(akshare_observations)
        by_code: dict[str, ConstituentDailyObservation] = {}
        for item in observations:
            if not isinstance(item, ConstituentDailyObservation):
                raise TypeError("akshare_observations must contain ConstituentDailyObservation")
            code = _normalize_code(item.code)
            if code in by_code:
                raise ValueError(f"duplicate AkShare observation for {code}")
            by_code[code] = item
        selected = tuple(by_code[code] for code in codes if code in by_code)
        metrics = _metrics(codes, selected)
        if not codes:
            return self._insufficient(sector_code, trading_date, metrics, "constituent denominator is zero")
        if len(selected) != len(codes):
            return self._insufficient(sector_code, trading_date, metrics, "required constituent fields are missing")
        if any(item.previous_five_day_average_volume <= 0 for item in selected):
            return self._insufficient(sector_code, trading_date, metrics, "relative-volume denominator is zero")
        if metrics.limit_balance is None or metrics.limit_activity is None:
            return self._insufficient(sector_code, trading_date, metrics, "limit-count denominator is zero")

        consistent = metrics.limit_balance != 0
        consensus_state = "一致" if consistent else "分歧"
        consensus_direction = (
            "转强" if consistent and metrics.limit_balance > 0
            else "转弱" if consistent and metrics.limit_balance < 0
            else "未确认"
        )
        expanding = (
            metrics.highest_streak_stock_count >= 2
            and metrics.relative_volume > 1.0
            and metrics.limit_balance > 0
        )
        cycle_position, event = _cycle_from(trend_state, expanding, consensus_direction)
        return SectorV2Label(
            sector_code, trading_date, "labeled", metrics, cycle_position,
            consensus_state, consensus_direction, event, self.rule_hash, self.version,
        )

    def _insufficient(self, sector_code: str, trading_date: str, metrics: SectorV2Metrics, reason: str) -> SectorV2Label:
        return SectorV2Label(
            sector_code, trading_date, "data_insufficient", metrics, None, None,
            None, None, self.rule_hash, self.version, reason,
        )


def _metrics(codes: tuple[str, ...], observations: tuple[ConstituentDailyObservation, ...]) -> SectorV2Metrics:
    count = len(codes)
    if not observations:
        return SectorV2Metrics(count, None, None, None, 0, 0, None, None, None)
    highest = max(item.limit_streak for item in observations)
    at_highest = sum(item.limit_streak == highest for item in observations) if highest > 0 else 0
    rises = sum(item.is_rise_limit for item in observations)
    falls = sum(item.is_fall_limit for item in observations)
    limit_total = rises + falls
    ratios = [
        item.volume / item.previous_five_day_average_volume
        for item in observations if item.previous_five_day_average_volume > 0
    ]
    return SectorV2Metrics(
        constituent_count=count,
        highest_limit_streak=highest,
        highest_streak_stock_count=at_highest,
        highest_streak_constituent_ratio=at_highest / count if count else None,
        rise_limit_count=rises,
        fall_limit_count=falls,
        limit_balance=(rises - falls) / limit_total if limit_total else None,
        limit_activity=limit_total / count if count else None,
        relative_volume=sum(ratios) / len(ratios) if len(ratios) == len(observations) else None,
    )


def _cycle_from(trend: TrendState, expanding: bool, direction: str) -> tuple[str, str]:
    if trend is TrendState.LOW_DORMANT:
        return "冰点", "无"
    if trend is TrendState.LOW_TURNING:
        return "启动", "无"
    if trend is TrendState.UP_CONFIRMED:
        return ("发酵", "二次启动" if expanding else "无")
    if trend is TrendState.HIGH_ACCELERATING:
        return ("高潮" if expanding else "发酵", "加速" if expanding else "平台整理")
    if trend is TrendState.HIGH_REVERSING:
        return (
            ("退潮", "破位转弱")
            if direction == "转弱"
            else ("发酵", "平台整理")
        )
    if trend is TrendState.DOWN:
        return "退潮", "破位转弱" if direction == "转弱" else "高位兑现"
    raise ValueError("unknown trend state")


def _normalize_code(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("constituent codes must be non-empty strings")
    return value.strip().upper().replace(".", "")[-6:]


def _validate_config(config: object) -> None:
    if not isinstance(config, dict) or set(config) != {"version", "required_fields", "cutover", "rule_hash"}:
        raise ValueError("invalid sector labeler v2 configuration")
    if isinstance(config["version"], bool) or not isinstance(config["version"], int) or config["version"] < 1:
        raise ValueError("sector labeler v2 version must be positive")


__all__ = [
    "ConstituentDailyObservation", "SectorLabelerV2", "SectorV2Label",
    "SectorV2Metrics", "TrendState",
]
