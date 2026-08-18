"""Programmatic T+1 gate over B/C probabilities and holding-lot age.

For the long-only A-share workflow, the canonical B-class opening buckets are
interpreted as follows: ``gap_up`` is favorable, ``near_reference`` is neutral,
and ``gap_down`` is adverse.  C-class ``target_first`` is favorable while
``stop_first`` is adverse; the remainder is the probability of touching
neither level.  Thresholds are explicit policy inputs instead of model output.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum
import math
from pathlib import Path
from typing import Any

import yaml

from .models import DecisionPoint, InsufficientData, ProbabilityResult, ProbabilityType
from .opening_distribution import OpeningDistributionResult
from .t1_first_passage import T1FirstPassageEstimate


TARGET_FIRST = "target_reached_before_stop_loss"
STOP_FIRST = "stop_loss_reached_before_target"
DEFAULT_T1_GATE_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "t1_gate.yaml"
)


class T1GateStatus(str, Enum):
    """Auditable result states; only PASSED permits a new buy."""

    PASSED = "passed"
    BLOCKED = "blocked"
    INSUFFICIENT_DATA = "insufficient_data"


class ExecutableActionKind(str, Enum):
    """Actions whose timing is fixed by the decision point and T+1 rules."""

    SELL_ELIGIBLE_THIS_AFTERNOON = "sell_eligible_this_afternoon"
    PLAN_BUY_THIS_AFTERNOON = "plan_buy_this_afternoon"
    PLAN_SELL_NEXT_TRADING_DAY = "plan_sell_next_trading_day"
    PLAN_BUY_NEXT_TRADING_DAY = "plan_buy_next_trading_day"


@dataclass(frozen=True, slots=True)
class T1GateConfig:
    """A validated, versioned gate policy loaded from external configuration."""

    minimum_target_first_probability: float
    maximum_stop_first_probability: float
    maximum_adverse_opening_probability: float
    favorable_opening_outcomes: frozenset[str]
    neutral_opening_outcomes: frozenset[str]
    adverse_opening_outcomes: frozenset[str]
    require_target_over_non_target: bool
    require_non_adverse_over_adverse: bool
    config_version: int

    def __post_init__(self) -> None:
        _require_probability(
            self.minimum_target_first_probability,
            "minimum_target_first_probability",
        )
        _require_probability(
            self.maximum_stop_first_probability,
            "maximum_stop_first_probability",
        )
        _require_probability(
            self.maximum_adverse_opening_probability,
            "maximum_adverse_opening_probability",
        )
        _require_outcome_group(
            self.favorable_opening_outcomes,
            "favorable_opening_outcomes",
            allow_empty=False,
        )
        _require_outcome_group(
            self.neutral_opening_outcomes,
            "neutral_opening_outcomes",
            allow_empty=True,
        )
        _require_outcome_group(
            self.adverse_opening_outcomes,
            "adverse_opening_outcomes",
            allow_empty=False,
        )
        groups = (
            self.favorable_opening_outcomes,
            self.neutral_opening_outcomes,
            self.adverse_opening_outcomes,
        )
        if any(
            left & right
            for index, left in enumerate(groups)
            for right in groups[index + 1 :]
        ):
            raise ValueError("opening outcome groups must be disjoint")
        if not isinstance(self.require_target_over_non_target, bool):
            raise TypeError("require_target_over_non_target must be a bool")
        if not isinstance(self.require_non_adverse_over_adverse, bool):
            raise TypeError("require_non_adverse_over_adverse must be a bool")
        if isinstance(self.config_version, bool) or not isinstance(
            self.config_version, int
        ):
            raise TypeError("config_version must be a positive integer")
        if self.config_version < 1:
            raise ValueError("config_version must be a positive integer")


def load_t1_gate_config(
    path: Path | str = DEFAULT_T1_GATE_CONFIG_PATH,
) -> T1GateConfig:
    """Load and validate a versioned T+1 gate policy from YAML."""
    source = Path(path)
    try:
        with source.open(encoding="utf-8") as config_file:
            raw = yaml.safe_load(config_file)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load T+1 gate config {source}: {exc}") from exc

    root = _config_mapping(
        raw,
        "t1_gate",
        {"version", "thresholds", "opening_outcomes", "dominance"},
    )
    thresholds = _config_mapping(
        root["thresholds"],
        "thresholds",
        {
            "minimum_target_first_probability",
            "maximum_stop_first_probability",
            "maximum_adverse_opening_probability",
        },
    )
    opening_outcomes = _config_mapping(
        root["opening_outcomes"],
        "opening_outcomes",
        {"favorable", "neutral", "adverse"},
    )
    dominance = _config_mapping(
        root["dominance"],
        "dominance",
        {
            "require_target_over_non_target",
            "require_non_adverse_over_adverse",
        },
    )
    return T1GateConfig(
        minimum_target_first_probability=thresholds[
            "minimum_target_first_probability"
        ],
        maximum_stop_first_probability=thresholds[
            "maximum_stop_first_probability"
        ],
        maximum_adverse_opening_probability=thresholds[
            "maximum_adverse_opening_probability"
        ],
        favorable_opening_outcomes=_configured_outcomes(
            opening_outcomes["favorable"], "opening_outcomes.favorable"
        ),
        neutral_opening_outcomes=_configured_outcomes(
            opening_outcomes["neutral"], "opening_outcomes.neutral"
        ),
        adverse_opening_outcomes=_configured_outcomes(
            opening_outcomes["adverse"], "opening_outcomes.adverse"
        ),
        require_target_over_non_target=dominance[
            "require_target_over_non_target"
        ],
        require_non_adverse_over_adverse=dominance[
            "require_non_adverse_over_adverse"
        ],
        config_version=root["version"],
    )


@dataclass(frozen=True, slots=True)
class HoldingLot:
    """A position lot; its acquisition timestamp determines T+1 sellability."""

    acquired_at: datetime
    quantity: int

    def __post_init__(self) -> None:
        if not isinstance(self.acquired_at, datetime):
            raise TypeError("acquired_at must be a datetime")
        if self.acquired_at.tzinfo is not None:
            raise ValueError("acquired_at must be a naive local-market datetime")
        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity <= 0
        ):
            raise ValueError("quantity must be a positive integer")


@dataclass(frozen=True, slots=True)
class ExecutableAction:
    """One institutionally executable action and its known sell quantity."""

    kind: ExecutableActionKind
    quantity: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ExecutableActionKind):
            raise TypeError("kind must be an ExecutableActionKind")
        is_sell = self.kind in {
            ExecutableActionKind.SELL_ELIGIBLE_THIS_AFTERNOON,
            ExecutableActionKind.PLAN_SELL_NEXT_TRADING_DAY,
        }
        if is_sell and (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity <= 0
        ):
            raise ValueError("sell actions must carry a positive quantity")
        if not is_sell and self.quantity is not None:
            raise ValueError("buy plans cannot choose position quantity")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "quantity": self.quantity}


@dataclass(frozen=True, slots=True)
class T1GateRequest:
    """All deterministic inputs needed by the gate at one decision point."""

    trading_date: date
    decision_point: DecisionPoint
    holdings: tuple[HoldingLot, ...]
    opening_distribution: OpeningDistributionResult
    first_passage: T1FirstPassageEstimate


@dataclass(frozen=True, slots=True)
class T1GateResult:
    """Independent gate decision plus actions the T+1 rules actually allow."""

    status: T1GateStatus
    decision_point: DecisionPoint
    executable_actions: tuple[ExecutableAction, ...]
    reason: str
    favorable_opening_probability: float | None
    neutral_opening_probability: float | None
    adverse_opening_probability: float | None
    target_first_probability: float | None
    stop_first_probability: float | None
    neither_probability: float | None
    config_version: int
    model_override_allowed: bool = field(default=False, init=False)

    @property
    def gate_passed(self) -> bool:
        return self.status is T1GateStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "gate_passed": self.gate_passed,
            "model_override_allowed": self.model_override_allowed,
            "decision_point": self.decision_point.value,
            "executable_actions": [
                action.to_dict() for action in self.executable_actions
            ],
            "reason": self.reason,
            "favorable_opening_probability": self.favorable_opening_probability,
            "neutral_opening_probability": self.neutral_opening_probability,
            "adverse_opening_probability": self.adverse_opening_probability,
            "target_first_probability": self.target_first_probability,
            "stop_first_probability": self.stop_first_probability,
            "neither_probability": self.neither_probability,
            "config_version": self.config_version,
        }


class T1GateCalculator:
    """Apply probability policy without accepting any model override."""

    def __init__(self, config: T1GateConfig) -> None:
        if not isinstance(config, T1GateConfig):
            raise TypeError("config must be a T1GateConfig")
        self._config = config

    def evaluate(self, request: T1GateRequest) -> T1GateResult:
        """Return the gate state and the actions legal at this decision point."""
        self._validate_request(request)
        sell_actions = _sell_actions(request)
        opening = _opening_probabilities(
            request.opening_distribution,
            request.decision_point,
            expected_outcomes=(
                self._config.favorable_opening_outcomes
                | self._config.neutral_opening_outcomes
                | self._config.adverse_opening_outcomes
            ),
        )
        target_first, stop_first = _first_passage_probabilities(
            request.first_passage, request.decision_point
        )
        if opening is None or target_first is None or stop_first is None:
            return T1GateResult(
                status=T1GateStatus.INSUFFICIENT_DATA,
                decision_point=request.decision_point,
                executable_actions=sell_actions,
                reason=(
                    "required B/C probability data is insufficient; "
                    "new buys are blocked"
                ),
                favorable_opening_probability=None,
                neutral_opening_probability=None,
                adverse_opening_probability=None,
                target_first_probability=None,
                stop_first_probability=None,
                neither_probability=None,
                config_version=self._config.config_version,
            )

        favorable_opening = sum(
            opening[outcome]
            for outcome in self._config.favorable_opening_outcomes
        )
        neutral_opening = sum(
            opening[outcome]
            for outcome in self._config.neutral_opening_outcomes
        )
        adverse_opening = sum(
            opening[outcome]
            for outcome in self._config.adverse_opening_outcomes
        )
        non_adverse_opening = favorable_opening + neutral_opening
        neither = max(0.0, 1.0 - target_first - stop_first)
        failures: list[str] = []
        if target_first < self._config.minimum_target_first_probability:
            failures.append("target-first probability is below its configured minimum")
        if stop_first > self._config.maximum_stop_first_probability:
            failures.append("stop-first probability exceeds its configured maximum")
        if (
            self._config.require_target_over_non_target
            and target_first <= stop_first + neither
        ):
            failures.append(
                "target-first probability does not dominate non-target outcomes"
            )
        if adverse_opening > self._config.maximum_adverse_opening_probability:
            failures.append(
                "adverse opening probability exceeds its configured maximum"
            )
        if (
            self._config.require_non_adverse_over_adverse
            and non_adverse_opening <= adverse_opening
        ):
            failures.append(
                "non-adverse opening probability does not dominate adverse opening"
            )

        passed = not failures
        actions = sell_actions
        if passed:
            actions += (_buy_action(request.decision_point),)
        return T1GateResult(
            status=T1GateStatus.PASSED if passed else T1GateStatus.BLOCKED,
            decision_point=request.decision_point,
            executable_actions=actions,
            reason=(
                "configured B/C probability policy passed"
                if passed
                else "; ".join(failures)
            ),
            favorable_opening_probability=favorable_opening,
            neutral_opening_probability=neutral_opening,
            adverse_opening_probability=adverse_opening,
            target_first_probability=target_first,
            stop_first_probability=stop_first,
            neither_probability=neither,
            config_version=self._config.config_version,
        )

    @staticmethod
    def _validate_request(request: T1GateRequest) -> None:
        if not isinstance(request, T1GateRequest):
            raise TypeError("request must be a T1GateRequest")
        if not isinstance(request.trading_date, date) or isinstance(
            request.trading_date, datetime
        ):
            raise TypeError("trading_date must be a date")
        if not isinstance(request.decision_point, DecisionPoint):
            raise TypeError("decision_point must be a DecisionPoint")
        if not isinstance(request.holdings, tuple) or any(
            not isinstance(lot, HoldingLot) for lot in request.holdings
        ):
            raise TypeError("holdings must be a tuple of HoldingLot values")
        decision_at = datetime.combine(
            request.trading_date,
            time(11, 30)
            if request.decision_point is DecisionPoint.MIDDAY
            else time(15, 0),
        )
        if any(lot.acquired_at > decision_at for lot in request.holdings):
            raise ValueError("holding acquisition cannot be after the decision point")
        if not isinstance(request.first_passage, T1FirstPassageEstimate):
            raise TypeError("first_passage must be a T1FirstPassageEstimate")


def _sell_actions(request: T1GateRequest) -> tuple[ExecutableAction, ...]:
    if request.decision_point is DecisionPoint.MIDDAY:
        quantity = sum(
            lot.quantity
            for lot in request.holdings
            if lot.acquired_at.date() < request.trading_date
        )
        kind = ExecutableActionKind.SELL_ELIGIBLE_THIS_AFTERNOON
    else:
        quantity = sum(lot.quantity for lot in request.holdings)
        kind = ExecutableActionKind.PLAN_SELL_NEXT_TRADING_DAY
    if quantity == 0:
        return ()
    return (ExecutableAction(kind=kind, quantity=quantity),)


def _buy_action(decision_point: DecisionPoint) -> ExecutableAction:
    kind = (
        ExecutableActionKind.PLAN_BUY_THIS_AFTERNOON
        if decision_point is DecisionPoint.MIDDAY
        else ExecutableActionKind.PLAN_BUY_NEXT_TRADING_DAY
    )
    return ExecutableAction(kind=kind, quantity=None)


def _opening_probabilities(
    result: OpeningDistributionResult,
    decision_point: DecisionPoint,
    *,
    expected_outcomes: frozenset[str],
) -> dict[str, float] | None:
    expected_kind = _distribution_kind(decision_point)
    if isinstance(result, InsufficientData):
        _validate_result_identity(result, ProbabilityType.OPENING_RANGE, decision_point)
        _validate_distribution_semantics(
            result, expected_kind, "opening distribution"
        )
        return None
    if not isinstance(result, tuple) or not result:
        raise TypeError("opening_distribution must be a non-empty result tuple")
    probabilities: dict[str, float] = {}
    for item in result:
        if not isinstance(item, ProbabilityResult):
            raise TypeError(
                "opening_distribution must contain ProbabilityResult values"
            )
        _validate_result_identity(item, ProbabilityType.OPENING_RANGE, decision_point)
        if item.outcome in probabilities:
            raise ValueError("opening distribution outcomes must be unique")
        probabilities[item.outcome] = item.probability
    if set(probabilities) != expected_outcomes:
        raise ValueError("opening distribution outcomes must match gate configuration")
    if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-9):
        raise ValueError("opening distribution probabilities must sum to 1")
    for item in result:
        _validate_distribution_semantics(
            item, expected_kind, "opening distribution"
        )
    return probabilities


def _first_passage_probabilities(
    estimate: T1FirstPassageEstimate,
    decision_point: DecisionPoint,
) -> tuple[float | None, float | None]:
    target = estimate.target_first
    stop = estimate.stop_first
    expected_kind = _distribution_kind(decision_point)
    if isinstance(target, InsufficientData) or isinstance(stop, InsufficientData):
        for item, expected_outcome in (
            (target, TARGET_FIRST),
            (stop, STOP_FIRST),
        ):
            _validate_result_identity(
                item, ProbabilityType.T1_FIRST_PASSAGE, decision_point
            )
            if item.outcome != expected_outcome:
                raise ValueError("first-passage result has an unexpected outcome")
            _validate_distribution_semantics(
                item, expected_kind, "first-passage result"
            )
        return None, None
    for item, expected_outcome in (
        (target, TARGET_FIRST),
        (stop, STOP_FIRST),
    ):
        if not isinstance(item, ProbabilityResult):
            raise TypeError("first-passage outcomes must be probability results")
        _validate_result_identity(
            item, ProbabilityType.T1_FIRST_PASSAGE, decision_point
        )
        if item.outcome != expected_outcome:
            raise ValueError("first-passage result has an unexpected outcome")
    if target.probability + stop.probability > 1.0 + 1e-9:
        raise ValueError("first-passage probabilities cannot sum above 1")
    _validate_distribution_semantics(target, expected_kind, "first-passage result")
    _validate_distribution_semantics(stop, expected_kind, "first-passage result")
    return target.probability, stop.probability


def _distribution_kind(decision_point: DecisionPoint) -> str:
    return (
        "intraday_next_bar"
        if decision_point is DecisionPoint.MIDDAY
        else "overnight_next_bar"
    )


def _validate_distribution_semantics(
    result: ProbabilityResult | InsufficientData,
    expected_kind: str,
    result_name: str,
) -> None:
    actual_kind = result.data_source.rpartition(":")[2]
    if actual_kind != expected_kind:
        raise ValueError(f"{result_name} uses the wrong decision-point semantics")


def _validate_result_identity(
    result: ProbabilityResult | InsufficientData,
    probability_type: ProbabilityType,
    decision_point: DecisionPoint,
) -> None:
    if not isinstance(result, (ProbabilityResult, InsufficientData)):
        raise TypeError("probability result has an unsupported type")
    if result.probability_type is not probability_type:
        raise ValueError("probability result has the wrong probability type")
    if result.decision_point is not decision_point:
        raise ValueError("probability result has the wrong decision point")


def _require_probability(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a probability")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be between 0 and 1")


def _require_outcome_group(
    value: object,
    field_name: str,
    *,
    allow_empty: bool,
) -> None:
    if not isinstance(value, frozenset) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise TypeError(f"{field_name} must be a frozenset of non-empty strings")
    if not allow_empty and not value:
        raise ValueError(f"{field_name} must not be empty")


def _config_mapping(
    value: object,
    field_name: str,
    expected_keys: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    actual_keys = set(value)
    if any(not isinstance(key, str) for key in actual_keys):
        raise TypeError(f"{field_name} keys must be strings")
    if actual_keys != expected_keys:
        missing = expected_keys - actual_keys
        extra = actual_keys - expected_keys
        details: list[str] = []
        if missing:
            details.append(f"missing {sorted(missing)!r}")
        if extra:
            details.append(f"unknown {sorted(extra)!r}")
        raise ValueError(f"{field_name} has invalid keys: {', '.join(details)}")
    return value


def _configured_outcomes(value: object, field_name: str) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} must be a sequence of outcome names")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise TypeError(f"{field_name} entries must be non-empty strings")
    outcomes = frozenset(value)
    _require_outcome_group(outcomes, field_name, allow_empty=True)
    if len(outcomes) != len(value):
        raise ValueError(f"{field_name} must not contain duplicates")
    return outcomes


__all__ = [
    "DEFAULT_T1_GATE_CONFIG_PATH",
    "ExecutableAction",
    "ExecutableActionKind",
    "HoldingLot",
    "T1GateCalculator",
    "T1GateConfig",
    "T1GateRequest",
    "T1GateResult",
    "T1GateStatus",
    "load_t1_gate_config",
]
