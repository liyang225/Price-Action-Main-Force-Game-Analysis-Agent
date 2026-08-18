"""B-class next-period distributions built from K_120M trading-day blocks."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
import math
import random
from types import MappingProxyType
from typing import TypeAlias

from src.data.models import Bar
from src.data.protocol import MarketDataSource

from .models import (
    DecisionPoint,
    InsufficientData,
    ProbabilityResult,
    ProbabilityType,
)


class OpeningDistributionKind(str, Enum):
    """The two physically distinct B-class sample populations."""

    INTRADAY_NEXT_BAR = "intraday_next_bar"
    OVERNIGHT_NEXT_BAR = "overnight_next_bar"


@dataclass(frozen=True, slots=True)
class OpeningRange:
    """One exhaustive half-open bucket of next-period returns."""

    outcome: str
    lower_bound: float | None
    upper_bound: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, str) or not self.outcome.strip():
            raise ValueError("outcome must be a non-empty string")
        for name, value in (
            ("lower_bound", self.lower_bound),
            ("upper_bound", self.upper_bound),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be a finite number or None")
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.lower_bound >= self.upper_bound
        ):
            raise ValueError("lower_bound must be less than upper_bound")

    def contains(self, period_return: float) -> bool:
        return (
            (self.lower_bound is None or period_return >= self.lower_bound)
            and (self.upper_bound is None or period_return < self.upper_bound)
        )


@dataclass(frozen=True, slots=True)
class OpeningDistributionConfig:
    """Injected statistical choices; no market threshold lives in code."""

    ranges: tuple[OpeningRange, ...]
    min_day_blocks: int
    bootstrap_iterations: int
    prior_strength: float
    priors: Mapping[OpeningDistributionKind, Mapping[str, float]]
    config_version: int
    random_seed: int

    def __post_init__(self) -> None:
        _validate_ranges(self.ranges)
        _require_positive_integer(self.min_day_blocks, "min_day_blocks")
        _require_positive_integer(self.bootstrap_iterations, "bootstrap_iterations")
        if (
            isinstance(self.prior_strength, bool)
            or not isinstance(self.prior_strength, (int, float))
            or not math.isfinite(self.prior_strength)
            or self.prior_strength < 0
        ):
            raise ValueError("prior_strength must be a finite non-negative number")
        if (
            isinstance(self.config_version, bool)
            or not isinstance(self.config_version, int)
            or self.config_version < 0
        ):
            raise ValueError("config_version must be a non-negative integer")
        if isinstance(self.random_seed, bool) or not isinstance(self.random_seed, int):
            raise ValueError("random_seed must be an integer")

        expected_kinds = set(OpeningDistributionKind)
        if set(self.priors) != expected_kinds:
            raise ValueError("priors must define intraday and overnight distributions")
        outcomes = {item.outcome for item in self.ranges}
        copied_priors: dict[OpeningDistributionKind, Mapping[str, float]] = {}
        for kind in OpeningDistributionKind:
            prior = dict(self.priors[kind])
            if set(prior) != outcomes:
                raise ValueError(f"{kind.value} prior outcomes must match configured ranges")
            for outcome, probability in prior.items():
                if (
                    isinstance(probability, bool)
                    or not isinstance(probability, (int, float))
                    or not math.isfinite(probability)
                    or not 0 <= probability <= 1
                ):
                    raise ValueError(
                        f"{kind.value} prior probability for {outcome!r} must be between 0 and 1"
                    )
            if not math.isclose(sum(prior.values()), 1.0, abs_tol=1e-9):
                raise ValueError(f"{kind.value} prior probabilities must sum to 1")
            copied_priors[kind] = MappingProxyType(prior)
        object.__setattr__(self, "priors", MappingProxyType(copied_priors))


OpeningDistributionResult: TypeAlias = tuple[ProbabilityResult, ...] | InsufficientData


@dataclass(frozen=True, slots=True)
class _TradingDayBlock:
    trading_date: date
    first_bar: Bar
    second_bar: Bar


@dataclass(frozen=True, slots=True)
class _OpeningObservation:
    day_blocks: tuple[_TradingDayBlock, ...]
    period_return: float

    @property
    def calendar_year(self) -> int:
        """Stratify by the year in which the predicted opening occurs."""
        return self.day_blocks[-1].trading_date.year


class OpeningDistributionEstimator:
    """Estimate the next complete K_120M period for one decision point."""

    def __init__(
        self,
        source: MarketDataSource,
        config: OpeningDistributionConfig,
    ) -> None:
        self._source = source
        self._config = config

    def estimate(
        self,
        code: str,
        decision_point: DecisionPoint,
        start: str,
        end: str,
    ) -> OpeningDistributionResult:
        """Return a next-period distribution or an explicit unavailable result."""
        if not isinstance(decision_point, DecisionPoint):
            raise TypeError("decision_point must be a DecisionPoint")
        kind = _kind_for(decision_point)
        bars = self._source.get_kline(code, "K_120M", start, end)
        observations = _opening_observations(bars, kind)
        data_source = f"historical_ohlcv:{kind.value}"
        if len(observations) < self._config.min_day_blocks:
            return InsufficientData(
                probability_type=ProbabilityType.OPENING_RANGE,
                outcome=f"{kind.value}_distribution",
                reason=(
                    f"{len(observations)} complete day blocks available; "
                    f"{self._config.min_day_blocks} required"
                ),
                data_source=data_source,
                config_version=self._config.config_version,
                decision_point=decision_point,
            )

        empirical = _bootstrap_probabilities(
            observations,
            self._config.ranges,
            self._config.bootstrap_iterations,
            self._config.random_seed + list(OpeningDistributionKind).index(kind),
        )
        prior_weight = self._config.prior_strength / (
            self._config.prior_strength + len(observations)
        )
        prior = self._config.priors[kind]
        probabilities = [
            (1 - prior_weight) * observed + prior_weight * prior[item.outcome]
            for item, observed in zip(self._config.ranges, empirical, strict=True)
        ]
        total = sum(probabilities)

        return tuple(
            ProbabilityResult(
                probability_type=ProbabilityType.OPENING_RANGE,
                outcome=item.outcome,
                probability=probability / total,
                prior_weight=prior_weight,
                config_version=self._config.config_version,
                decision_point=decision_point,
                data_source=data_source,
            )
            for item, probability in zip(
                self._config.ranges, probabilities, strict=True
            )
        )


def _kind_for(decision_point: DecisionPoint) -> OpeningDistributionKind:
    if decision_point is DecisionPoint.MIDDAY:
        return OpeningDistributionKind.INTRADAY_NEXT_BAR
    return OpeningDistributionKind.OVERNIGHT_NEXT_BAR


def _opening_observations(
    bars: Sequence[Bar], kind: OpeningDistributionKind
) -> list[_OpeningObservation]:
    day_groups: dict[date, list[tuple[datetime, Bar]]] = defaultdict(list)
    for bar in bars:
        timestamp = datetime.fromisoformat(bar.time_key)
        _validate_price(bar.open, "open", bar.time_key)
        _validate_price(bar.close, "close", bar.time_key)
        day_groups[timestamp.date()].append((timestamp, bar))

    ordered_days: list[_TradingDayBlock | None] = []
    for trading_date in sorted(day_groups):
        ordered_bars = sorted(day_groups[trading_date], key=lambda item: item[0])
        if len(ordered_bars) != 2:
            ordered_days.append(None)
            continue
        ordered_days.append(
            _TradingDayBlock(
                trading_date,
                ordered_bars[0][1],
                ordered_bars[1][1],
            )
        )

    if kind is OpeningDistributionKind.INTRADAY_NEXT_BAR:
        return [
            _OpeningObservation(
                day_blocks=(day,),
                period_return=_relative_return(
                    day.second_bar.close, day.first_bar.close
                ),
            )
            for day in ordered_days
            if day is not None
        ]

    observations: list[_OpeningObservation] = []
    for current_day, next_day in zip(ordered_days, ordered_days[1:]):
        if current_day is None or next_day is None:
            continue
        observations.append(
            _OpeningObservation(
                day_blocks=(current_day, next_day),
                period_return=_relative_return(
                    next_day.first_bar.close, current_day.second_bar.close
                ),
            )
        )
    return observations


def _relative_return(period_close: float, reference_close: float) -> float:
    return period_close / reference_close - 1.0


def _bootstrap_probabilities(
    observations: Sequence[_OpeningObservation],
    ranges: Sequence[OpeningRange],
    iterations: int,
    seed: int,
) -> list[float]:
    rng = random.Random(seed)
    counts = [0] * len(ranges)
    strata: dict[int, list[_OpeningObservation]] = defaultdict(list)
    for observation in observations:
        strata[observation.calendar_year].append(observation)

    for _ in range(iterations):
        for calendar_year in sorted(strata):
            stratum = strata[calendar_year]
            for _ in range(len(stratum)):
                observation = stratum[rng.randrange(len(stratum))]
                for index, opening_range in enumerate(ranges):
                    if opening_range.contains(observation.period_return):
                        counts[index] += 1
                        break
    total = iterations * len(observations)
    return [count / total for count in counts]


def _validate_ranges(ranges: tuple[OpeningRange, ...]) -> None:
    if not isinstance(ranges, tuple) or not ranges:
        raise ValueError("ranges must be a non-empty tuple")
    if len({item.outcome for item in ranges}) != len(ranges):
        raise ValueError("range outcomes must be unique")
    if ranges[0].lower_bound is not None or ranges[-1].upper_bound is not None:
        raise ValueError("ranges must cover all possible opening returns")
    for previous, current in zip(ranges, ranges[1:]):
        if previous.upper_bound != current.lower_bound:
            raise ValueError("ranges must be ordered, contiguous, and non-overlapping")


def _require_positive_integer(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")


def _validate_price(value: object, field_name: str, time_key: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{field_name} at {time_key!r} must be a positive finite number")
