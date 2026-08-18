"""Conditioned day-block bootstrap for T+1 first-passage probabilities.

The estimator keeps real-time features free of look-ahead: every historical
condition is built only from bars and expanding ranks available at that
decision point.  A complete condition cell is attempted first, followed by
the caller-configured degradation order.  No probability is returned unless
the selected cell reaches ``min_samples``.
"""

from __future__ import annotations

from bisect import bisect_right, insort
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
import math
import random
from statistics import pstdev
from typing import Any

from src.data.models import Bar
from src.data.protocol import MarketDataSource
from src.labeler_constants import CYCLE_STATES

from .models import (
    DecisionPoint,
    InsufficientData,
    ProbabilityCapabilityResult,
    ProbabilityResult,
    ProbabilityType,
)


DATA_SOURCE_PREFIX = "historical_ohlcv_day_block_bootstrap"
TARGET_FIRST = "target_reached_before_stop_loss"
STOP_FIRST = "stop_loss_reached_before_target"
QUANTILE_BUCKETS = frozenset({"low", "medium", "high"})
RETURN_BUCKETS = frozenset({"negative", "flat", "positive"})


class ConditionDimension(str, Enum):
    """Auditable dimensions of a T+1 conditional cell."""

    VOLATILITY_QUANTILE = "volatility_quantile"
    RECENT_RETURN = "recent_return"
    TURNOVER_QUANTILE = "turnover_quantile"
    CYCLE_STATE = "cycle_state"


FULL_CONDITION_DIMENSIONS = tuple(ConditionDimension)


class SameBarRule(str, Enum):
    """Conservative rule for OHLC bars with both levels inside their range."""

    STOP_LOSS_FIRST = "stop_loss_first"


class _Outcome(str, Enum):
    TARGET_FIRST = "target_first"
    STOP_FIRST = "stop_first"
    NEITHER = "neither"


@dataclass(frozen=True, slots=True)
class ConditionCell:
    """The condition values available at one decision point."""

    volatility_quantile: str | None
    recent_return: str | None
    turnover_quantile: str | None
    cycle_state: str | None

    def __post_init__(self) -> None:
        _require_optional_choice(
            self.volatility_quantile,
            QUANTILE_BUCKETS,
            "volatility_quantile",
        )
        _require_optional_choice(self.recent_return, RETURN_BUCKETS, "recent_return")
        _require_optional_choice(
            self.turnover_quantile,
            QUANTILE_BUCKETS,
            "turnover_quantile",
        )
        _require_optional_choice(self.cycle_state, CYCLE_STATES, "cycle_state")


@dataclass(frozen=True, slots=True)
class ConditionedFirstPassageSample:
    """One historical decision point and its intact first-passage bar block."""

    day: str
    condition: ConditionCell
    reference_price: float
    passage_bars: tuple[Bar, ...]

    def __post_init__(self) -> None:
        _require_text(self.day, "day")
        try:
            date.fromisoformat(self.day)
        except ValueError as exc:
            raise ValueError("day must be an ISO-8601 trading date") from exc
        if not isinstance(self.condition, ConditionCell):
            raise TypeError("condition must be a ConditionCell")
        _require_positive_number(self.reference_price, "reference_price")
        if not isinstance(self.passage_bars, tuple) or not self.passage_bars:
            raise ValueError("passage_bars must be a non-empty tuple")
        for bar in self.passage_bars:
            _validate_bar(bar)


@dataclass(frozen=True, slots=True)
class T1FirstPassageConfig:
    """Injected statistical parameters; no estimation threshold is hard-coded."""

    min_samples: int
    volatility_lookback: int
    recent_return_lookback: int
    volatility_quantiles: tuple[float, float]
    turnover_quantiles: tuple[float, float]
    recent_return_edges: tuple[float, float]
    degradation_order: tuple[ConditionDimension, ...]
    bootstrap_iterations: int
    random_seed: int
    config_version: int

    def __post_init__(self) -> None:
        _require_positive_integer(self.min_samples, "min_samples")
        _require_positive_integer(self.volatility_lookback, "volatility_lookback")
        _require_positive_integer(self.recent_return_lookback, "recent_return_lookback")
        _require_quantile_edges(self.volatility_quantiles, "volatility_quantiles")
        _require_quantile_edges(self.turnover_quantiles, "turnover_quantiles")
        _require_ordered_finite_pair(self.recent_return_edges, "recent_return_edges")
        if not isinstance(self.degradation_order, tuple):
            raise TypeError("degradation_order must be a tuple")
        if any(not isinstance(item, ConditionDimension) for item in self.degradation_order):
            raise TypeError("degradation_order entries must be ConditionDimension values")
        if len(set(self.degradation_order)) != len(self.degradation_order):
            raise ValueError("degradation_order must not contain duplicates")
        _require_positive_integer(self.bootstrap_iterations, "bootstrap_iterations")
        if isinstance(self.random_seed, bool) or not isinstance(self.random_seed, int):
            raise TypeError("random_seed must be an integer")
        if (
            isinstance(self.config_version, bool)
            or not isinstance(self.config_version, int)
            or self.config_version < 0
        ):
            raise ValueError("config_version must be a non-negative integer")

    def as_dict(self) -> dict[str, Any]:
        """Return constructor-compatible values for explicit config derivation."""
        return {
            "min_samples": self.min_samples,
            "volatility_lookback": self.volatility_lookback,
            "recent_return_lookback": self.recent_return_lookback,
            "volatility_quantiles": self.volatility_quantiles,
            "turnover_quantiles": self.turnover_quantiles,
            "recent_return_edges": self.recent_return_edges,
            "degradation_order": self.degradation_order,
            "bootstrap_iterations": self.bootstrap_iterations,
            "random_seed": self.random_seed,
            "config_version": self.config_version,
        }


@dataclass(frozen=True, slots=True)
class T1FirstPassageRequest:
    """A stock-level request evaluated at one current decision point."""

    code: str
    start: str
    end: str
    target_price: float
    stop_loss_price: float
    decision_point: DecisionPoint
    cycle_state_snapshots: Mapping[tuple[str, DecisionPoint], str | None]
    turnover_rate_snapshots: Mapping[tuple[str, DecisionPoint], float | None]


@dataclass(frozen=True, slots=True)
class T1FirstPassageEstimate:
    """Both competing first-passage results plus auditable cell metadata."""

    target_first: ProbabilityCapabilityResult
    stop_first: ProbabilityCapabilityResult
    sample_count: int
    condition_dimensions: tuple[str, ...]
    dropped_dimensions: tuple[str, ...]
    same_bar_rule: str = SameBarRule.STOP_LOSS_FIRST.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_first": self.target_first.to_dict(),
            "stop_first": self.stop_first.to_dict(),
            "sample_count": self.sample_count,
            "condition_dimensions": list(self.condition_dimensions),
            "dropped_dimensions": list(self.dropped_dimensions),
            "same_bar_rule": self.same_bar_rule,
        }


class T1FirstPassageEstimator:
    """Estimate next-day target/stop first passage through one market-data seam."""

    def __init__(
        self,
        source: MarketDataSource,
        config: T1FirstPassageConfig,
    ) -> None:
        self._source = source
        self._config = config

    def estimate(self, request: T1FirstPassageRequest) -> T1FirstPassageEstimate:
        """Fetch K_120M history, build leakage-free cells, and estimate the result."""
        self._validate_request(request)
        bars = self._source.get_kline(
            request.code,
            "K_120M",
            request.start,
            request.end,
        )
        samples, current_condition, current_reference = self._build_samples(
            bars,
            request.decision_point,
            request.cycle_state_snapshots,
            request.turnover_rate_snapshots,
        )
        if current_reference is None or current_condition is None:
            return self._insufficient(
                request.decision_point,
                sample_count=0,
                active_dimensions=FULL_CONDITION_DIMENSIONS,
                dropped_dimensions=(),
                reason="historical K_120M data cannot form a current condition cell",
            )

        target_return = request.target_price / current_reference - 1.0
        stop_loss_return = request.stop_loss_price / current_reference - 1.0
        return self.estimate_samples(
            samples,
            current_condition=current_condition,
            target_return=target_return,
            stop_loss_return=stop_loss_return,
            decision_point=request.decision_point,
        )

    def estimate_samples(
        self,
        samples: Sequence[ConditionedFirstPassageSample],
        *,
        current_condition: ConditionCell,
        target_return: float,
        stop_loss_return: float,
        decision_point: DecisionPoint,
    ) -> T1FirstPassageEstimate:
        """Estimate from already conditioned, whole-day historical blocks."""
        if not isinstance(current_condition, ConditionCell):
            raise TypeError("current_condition must be a ConditionCell")
        _require_return_levels(target_return, stop_loss_return)
        if not isinstance(decision_point, DecisionPoint):
            raise TypeError("decision_point must be a DecisionPoint")
        materialized = tuple(samples)
        if any(not isinstance(sample, ConditionedFirstPassageSample) for sample in materialized):
            raise TypeError("samples must contain ConditionedFirstPassageSample values")
        materialized = tuple(
            sample
            for sample in materialized
            if _is_complete_passage_window(sample, decision_point)
        )

        active = list(FULL_CONDITION_DIMENSIONS)
        dropped: list[ConditionDimension] = []
        matching: tuple[ConditionedFirstPassageSample, ...] = ()
        for drop_after_attempt in (*self._config.degradation_order, None):
            matching = self._matching_samples(materialized, current_condition, active)
            if len(matching) >= self._config.min_samples:
                return self._available(
                    matching,
                    target_return=target_return,
                    stop_loss_return=stop_loss_return,
                    decision_point=decision_point,
                    active_dimensions=tuple(active),
                    dropped_dimensions=tuple(dropped),
                )
            if drop_after_attempt is None:
                break
            if drop_after_attempt in active:
                active.remove(drop_after_attempt)
                dropped.append(drop_after_attempt)

        return self._insufficient(
            decision_point,
            sample_count=len(matching),
            active_dimensions=tuple(active),
            dropped_dimensions=tuple(dropped),
            reason=(
                "matching historical sample is below the required threshold "
                f"after configured degradation ({len(matching)} < {self._config.min_samples})"
            ),
        )

    def _available(
        self,
        samples: tuple[ConditionedFirstPassageSample, ...],
        *,
        target_return: float,
        stop_loss_return: float,
        decision_point: DecisionPoint,
        active_dimensions: tuple[ConditionDimension, ...],
        dropped_dimensions: tuple[ConditionDimension, ...],
    ) -> T1FirstPassageEstimate:
        outcomes = tuple(
            _first_passage_outcome(sample, target_return, stop_loss_return)
            for sample in samples
        )
        target_probability, stop_probability = self._day_block_bootstrap(outcomes)
        common = {
            "probability_type": ProbabilityType.T1_FIRST_PASSAGE,
            "prior_weight": 0.0,
            "config_version": self._config.config_version,
            "decision_point": decision_point,
            "data_source": _data_source(decision_point),
        }
        return T1FirstPassageEstimate(
            target_first=ProbabilityResult(
                outcome=TARGET_FIRST,
                probability=target_probability,
                **common,
            ),
            stop_first=ProbabilityResult(
                outcome=STOP_FIRST,
                probability=stop_probability,
                **common,
            ),
            sample_count=len(samples),
            condition_dimensions=tuple(item.value for item in active_dimensions),
            dropped_dimensions=tuple(item.value for item in dropped_dimensions),
        )

    def _insufficient(
        self,
        decision_point: DecisionPoint,
        *,
        sample_count: int,
        active_dimensions: tuple[ConditionDimension, ...],
        dropped_dimensions: tuple[ConditionDimension, ...],
        reason: str,
    ) -> T1FirstPassageEstimate:
        common = {
            "probability_type": ProbabilityType.T1_FIRST_PASSAGE,
            "reason": reason,
            "data_source": _data_source(decision_point),
            "config_version": self._config.config_version,
            "decision_point": decision_point,
        }
        return T1FirstPassageEstimate(
            target_first=InsufficientData(outcome=TARGET_FIRST, **common),
            stop_first=InsufficientData(outcome=STOP_FIRST, **common),
            sample_count=sample_count,
            condition_dimensions=tuple(item.value for item in active_dimensions),
            dropped_dimensions=tuple(item.value for item in dropped_dimensions),
        )

    @staticmethod
    def _matching_samples(
        samples: tuple[ConditionedFirstPassageSample, ...],
        current: ConditionCell,
        active_dimensions: list[ConditionDimension],
    ) -> tuple[ConditionedFirstPassageSample, ...]:
        if any(getattr(current, item.value) is None for item in active_dimensions):
            return ()
        return tuple(
            sample
            for sample in samples
            if all(
                getattr(sample.condition, item.value) == getattr(current, item.value)
                for item in active_dimensions
            )
        )

    def _day_block_bootstrap(
        self, outcomes: tuple[_Outcome, ...]
    ) -> tuple[float, float]:
        rng = random.Random(self._config.random_seed)
        target_count = 0
        stop_count = 0
        sample_count = len(outcomes)
        for _ in range(self._config.bootstrap_iterations):
            for _ in range(sample_count):
                outcome = outcomes[rng.randrange(sample_count)]
                target_count += outcome is _Outcome.TARGET_FIRST
                stop_count += outcome is _Outcome.STOP_FIRST
        denominator = self._config.bootstrap_iterations * sample_count
        return target_count / denominator, stop_count / denominator

    def _build_samples(
        self,
        bars: Sequence[Bar],
        decision_point: DecisionPoint,
        cycle_state_snapshots: Mapping[tuple[str, DecisionPoint], str | None],
        turnover_rate_snapshots: Mapping[tuple[str, DecisionPoint], float | None],
    ) -> tuple[tuple[ConditionedFirstPassageSample, ...], ConditionCell | None, float | None]:
        ordered = sorted(
            ((_parse_time_key(bar.time_key), bar) for bar in bars),
            key=lambda item: item[0],
        )
        for _, bar in ordered:
            _validate_bar(bar)
        grouped: dict[str, list[tuple[int, Bar]]] = defaultdict(list)
        for index, (timestamp, bar) in enumerate(ordered):
            grouped[timestamp.date().isoformat()].append((index, bar))
        days = sorted(grouped)
        if len(days) < 2:
            return (), None, None

        current_day = days[-1]
        if not _has_current_decision_bar(grouped[current_day], decision_point):
            return (), None, None

        feature_grouped: dict[str, list[tuple[int, Bar]]] = defaultdict(list)
        feature_bars: list[Bar] = []
        for day in days:
            if day == current_day:
                source_bars = (
                    grouped[day][:1]
                    if decision_point is DecisionPoint.MIDDAY
                    else grouped[day]
                )
            elif _is_complete_k120_day(grouped[day]):
                source_bars = grouped[day]
            else:
                continue
            for _, bar in source_bars:
                feature_grouped[day].append((len(feature_bars), bar))
                feature_bars.append(bar)

        valid_feature_days = [day for day in days if day in feature_grouped]
        closes = [bar.close for bar in feature_bars]
        raw_volatility: dict[str, float | None] = {}
        raw_turnover_rates: dict[str, float | None] = {}
        recent_returns: dict[str, float | None] = {}
        references: dict[str, float] = {}
        for day in valid_feature_days:
            decision_index, decision_bar = _decision_bar(
                feature_grouped[day], decision_point
            )
            references[day] = decision_bar.close
            turnover_rate = turnover_rate_snapshots.get((day, decision_point))
            if turnover_rate is not None:
                _require_non_negative_number(turnover_rate, f"turnover_rates[{day!r}]")
                turnover_rate = float(turnover_rate)
            raw_turnover_rates[day] = turnover_rate
            if decision_index >= self._config.volatility_lookback:
                returns = [
                    closes[index] / closes[index - 1] - 1.0
                    for index in range(
                        decision_index - self._config.volatility_lookback + 1,
                        decision_index + 1,
                    )
                ]
                raw_volatility[day] = pstdev(returns)
            else:
                raw_volatility[day] = None
            lookback = self._config.recent_return_lookback
            recent_returns[day] = (
                decision_bar.close / closes[decision_index - lookback] - 1.0
                if decision_index >= lookback
                else None
            )

        volatility_history: list[float] = []
        turnover_history: list[float] = []
        conditions: dict[str, ConditionCell] = {}
        for day in valid_feature_days:
            volatility = raw_volatility[day]
            conditions[day] = ConditionCell(
                volatility_quantile=_expanding_quantile_bucket(
                    volatility,
                    volatility_history,
                    self._config.volatility_quantiles,
                ),
                recent_return=_return_bucket(
                    recent_returns[day], self._config.recent_return_edges
                ),
                turnover_quantile=_expanding_quantile_bucket(
                    raw_turnover_rates[day],
                    turnover_history,
                    self._config.turnover_quantiles,
                ),
                cycle_state=cycle_state_snapshots.get((day, decision_point)),
            )
            if volatility is not None:
                insort(volatility_history, volatility)
            turnover_rate = raw_turnover_rates[day]
            if turnover_rate is not None:
                insort(turnover_history, turnover_rate)

        if decision_point is DecisionPoint.MIDDAY:
            samples = tuple(
                ConditionedFirstPassageSample(
                    day=day,
                    condition=conditions[day],
                    reference_price=references[day],
                    passage_bars=(grouped[day][1][1],),
                )
                for day in days[:-1]
                if _is_complete_k120_day(grouped[day])
            )
        else:
            samples = tuple(
                ConditionedFirstPassageSample(
                    day=day,
                    condition=conditions[day],
                    reference_price=references[day],
                    passage_bars=tuple(bar for _, bar in grouped[next_day]),
                )
                for day, next_day in zip(days, days[1:])
                if _is_complete_k120_day(grouped[day])
                and _is_complete_k120_day(grouped[next_day])
            )
        return samples, conditions[current_day], references[current_day]

    @staticmethod
    def _validate_request(request: T1FirstPassageRequest) -> None:
        if not isinstance(request, T1FirstPassageRequest):
            raise TypeError("request must be a T1FirstPassageRequest")
        _require_text(request.code, "code")
        _require_text(request.start, "start")
        _require_text(request.end, "end")
        _require_positive_number(request.target_price, "target_price")
        _require_positive_number(request.stop_loss_price, "stop_loss_price")
        if not isinstance(request.decision_point, DecisionPoint):
            raise TypeError("decision_point must be a DecisionPoint")
        _validate_cycle_state_snapshots(request.cycle_state_snapshots)
        _validate_turnover_rate_snapshots(request.turnover_rate_snapshots)


def _decision_bar(
    indexed_bars: list[tuple[int, Bar]], decision_point: DecisionPoint
) -> tuple[int, Bar]:
    if decision_point is DecisionPoint.MIDDAY:
        return indexed_bars[0]
    return indexed_bars[-1]


def _is_complete_k120_day(indexed_bars: list[tuple[int, Bar]]) -> bool:
    return (
        len(indexed_bars) == 2
        and _bar_time(indexed_bars[0][1]) == time(11, 30)
        and _bar_time(indexed_bars[1][1]) == time(15, 0)
    )


def _has_current_decision_bar(
    indexed_bars: list[tuple[int, Bar]], decision_point: DecisionPoint
) -> bool:
    if decision_point is DecisionPoint.MIDDAY:
        return bool(indexed_bars) and _bar_time(indexed_bars[0][1]) == time(11, 30)
    return _is_complete_k120_day(indexed_bars)


def _is_complete_passage_window(
    sample: ConditionedFirstPassageSample, decision_point: DecisionPoint
) -> bool:
    bars = sample.passage_bars
    decision_day = date.fromisoformat(sample.day)
    if decision_point is DecisionPoint.MIDDAY:
        return (
            len(bars) == 1
            and _bar_time(bars[0]) == time(15, 0)
            and _bar_date(bars[0]) == decision_day
        )
    return (
        len(bars) == 2
        and _bar_time(bars[0]) == time(11, 30)
        and _bar_time(bars[1]) == time(15, 0)
        and _bar_date(bars[0]) == _bar_date(bars[1])
        and _bar_date(bars[0]) > decision_day
    )


def _bar_time(bar: Bar) -> time:
    return _parse_time_key(bar.time_key).time().replace(tzinfo=None)


def _bar_date(bar: Bar) -> date:
    return _parse_time_key(bar.time_key).date()


def _data_source(decision_point: DecisionPoint) -> str:
    distribution = (
        "intraday_next_bar"
        if decision_point is DecisionPoint.MIDDAY
        else "overnight_next_bar"
    )
    return f"{DATA_SOURCE_PREFIX}:{distribution}"


def _first_passage_outcome(
    sample: ConditionedFirstPassageSample,
    target_return: float,
    stop_loss_return: float,
) -> _Outcome:
    target = sample.reference_price * (1.0 + target_return)
    stop = sample.reference_price * (1.0 + stop_loss_return)
    short_plan = target_return < 0
    for bar in sample.passage_bars:
        if short_plan:
            target_hit = bar.low <= target
            stop_hit = bar.high >= stop
        else:
            target_hit = bar.high >= target
            stop_hit = bar.low <= stop
        if target_hit and stop_hit:
            return _Outcome.STOP_FIRST
        if target_hit:
            return _Outcome.TARGET_FIRST
        if stop_hit:
            return _Outcome.STOP_FIRST
    return _Outcome.NEITHER


def _expanding_quantile_bucket(
    value: float | None,
    sorted_history: list[float],
    edges: tuple[float, float],
) -> str | None:
    if value is None or not sorted_history:
        return None
    percentile = bisect_right(sorted_history, value) / len(sorted_history)
    if percentile <= edges[0]:
        return "low"
    if percentile <= edges[1]:
        return "medium"
    return "high"


def _return_bucket(
    value: float | None, edges: tuple[float, float]
) -> str | None:
    if value is None:
        return None
    if value < edges[0]:
        return "negative"
    if value > edges[1]:
        return "positive"
    return "flat"


def _parse_time_key(value: str) -> datetime:
    _require_text(value, "bar.time_key")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"bar.time_key is not ISO-8601 compatible: {value!r}") from exc


def _validate_bar(bar: Bar) -> None:
    if not isinstance(bar, Bar):
        raise TypeError("K-line history must contain Bar values")
    for field_name in ("open", "high", "low", "close"):
        _require_positive_number(getattr(bar, field_name), f"bar.{field_name}")
    if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
        raise ValueError("bar OHLC range is inconsistent")
    if bar.low > bar.high:
        raise ValueError("bar.low must not exceed bar.high")
    if isinstance(bar.turnover, bool) or not isinstance(bar.turnover, (int, float)):
        raise TypeError("bar.turnover must be a finite non-negative number")
    if not math.isfinite(bar.turnover) or bar.turnover < 0:
        raise ValueError("bar.turnover must be a finite non-negative number")


def _require_return_levels(target_return: float, stop_loss_return: float) -> None:
    if (
        isinstance(target_return, bool)
        or not isinstance(target_return, (int, float))
        or not math.isfinite(target_return)
        or isinstance(stop_loss_return, bool)
        or not isinstance(stop_loss_return, (int, float))
        or not math.isfinite(stop_loss_return)
    ):
        raise ValueError("target_return must be finite numbers")
    long_plan = target_return > 0 and -1 < stop_loss_return < 0
    short_plan = -1 < target_return < 0 and stop_loss_return > 0
    if not (long_plan or short_plan):
        raise ValueError(
            "target_return must be opposite in direction to stop_loss_return "
            "for a valid long or short plan"
        )


def _require_quantile_edges(value: object, field_name: str) -> None:
    _require_ordered_finite_pair(value, field_name)
    low, high = value
    if not 0 < low < high < 1:
        raise ValueError(f"{field_name} values must satisfy 0 < low < high < 1")


def _require_ordered_finite_pair(value: object, field_name: str) -> None:
    if not isinstance(value, tuple) or len(value) != 2:
        raise TypeError(f"{field_name} must be a two-value tuple")
    low, high = value
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(item)
        for item in value
    ):
        raise ValueError(f"{field_name} values must be finite numbers")
    if low >= high:
        raise ValueError(f"{field_name} values must be strictly increasing")


def _require_positive_integer(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _require_positive_number(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a finite positive number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field_name} must be a finite positive number")


def _require_non_negative_number(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a finite non-negative number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field_name} must be a finite non-negative number")


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")


def _require_optional_choice(
    value: object,
    choices: frozenset[str],
    field_name: str,
) -> None:
    if value is not None and (not isinstance(value, str) or value not in choices):
        raise ValueError(
            f"{field_name} must be one of {sorted(choices)!r} or None"
        )


def _validate_cycle_state_snapshots(value: object) -> None:
    if not isinstance(value, Mapping):
        raise TypeError("cycle_state_snapshots must be a mapping")
    for key, cycle_state in value.items():
        _validate_snapshot_key(key, "cycle_state_snapshots")
        _require_optional_choice(cycle_state, CYCLE_STATES, "cycle_state")


def _validate_turnover_rate_snapshots(value: object) -> None:
    if not isinstance(value, Mapping):
        raise TypeError("turnover_rate_snapshots must be a mapping")
    for key, turnover_rate in value.items():
        _validate_snapshot_key(key, "turnover_rate_snapshots")
        if turnover_rate is not None:
            _require_non_negative_number(turnover_rate, "turnover_rate")


def _validate_snapshot_key(value: object, field_name: str) -> None:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError(
            f"{field_name} keys must be (trading_date, decision_point) tuples"
        )
    trading_date, decision_point = value
    _require_text(trading_date, f"{field_name} trading_date")
    if not isinstance(decision_point, DecisionPoint):
        raise ValueError(f"{field_name} keys must contain a DecisionPoint")


__all__ = [
    "ConditionCell",
    "ConditionDimension",
    "ConditionedFirstPassageSample",
    "SameBarRule",
    "T1FirstPassageConfig",
    "T1FirstPassageEstimate",
    "T1FirstPassageEstimator",
    "T1FirstPassageRequest",
]
