"""Serializable public results shared by every probability capability."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, TypeAlias


class ProbabilityType(str, Enum):
    """The three probability classes defined by the system architecture."""

    BEHAVIOR = "A"
    OPENING_RANGE = "B"
    T1_FIRST_PASSAGE = "C"


class DecisionPoint(str, Enum):
    """The two trading-day decision points with distinct probability semantics."""

    MIDDAY = "午盘"
    CLOSE = "收盘"


class ResultStatus(str, Enum):
    """Discriminants for callers handling probability capability results."""

    AVAILABLE = "available"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class ProbabilityResult:
    """A usable probability for one named outcome at one decision point."""

    probability_type: ProbabilityType
    outcome: str
    probability: float
    prior_weight: float
    config_version: int
    decision_point: DecisionPoint
    data_source: str
    status: ResultStatus = field(default=ResultStatus.AVAILABLE, init=False)

    def __post_init__(self) -> None:
        _require_enum(self.probability_type, ProbabilityType, "probability_type")
        _require_non_empty_text(self.outcome, "outcome")
        _require_probability(self.probability, "probability")
        _require_probability(self.prior_weight, "prior_weight")
        _require_config_version(self.config_version)
        _require_enum(self.decision_point, DecisionPoint, "decision_point")
        _require_non_empty_text(self.data_source, "data_source")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation for UI, PA, and persistence consumers."""
        from .disclaimer import annotate_probability_row

        return annotate_probability_row({
            "status": self.status.value,
            "probability_type": self.probability_type.value,
            "outcome": self.outcome,
            "probability": self.probability,
            "prior_weight": self.prior_weight,
            "config_version": self.config_version,
            "decision_point": self.decision_point.value,
            "data_source": self.data_source,
        })

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProbabilityResult:
        """Recreate a valid result from its serialized representation."""
        _require_status(value, ResultStatus.AVAILABLE)
        return cls(
            probability_type=ProbabilityType(value["probability_type"]),
            outcome=value["outcome"],
            probability=value["probability"],
            prior_weight=value["prior_weight"],
            config_version=value["config_version"],
            decision_point=DecisionPoint(value["decision_point"]),
            data_source=value["data_source"],
        )


@dataclass(frozen=True, slots=True)
class InsufficientData:
    """An explicit unavailable result that deliberately contains no probability."""

    probability_type: ProbabilityType
    outcome: str
    reason: str
    data_source: str
    config_version: int
    decision_point: DecisionPoint
    status: ResultStatus = field(default=ResultStatus.INSUFFICIENT_DATA, init=False)

    def __post_init__(self) -> None:
        _require_enum(self.probability_type, ProbabilityType, "probability_type")
        _require_non_empty_text(self.outcome, "outcome")
        _require_non_empty_text(self.reason, "reason")
        _require_non_empty_text(self.data_source, "data_source")
        _require_config_version(self.config_version)
        _require_enum(self.decision_point, DecisionPoint, "decision_point")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe unavailable result without a probability field."""
        return {
            "status": self.status.value,
            "probability_type": self.probability_type.value,
            "outcome": self.outcome,
            "reason": self.reason,
            "data_source": self.data_source,
            "config_version": self.config_version,
            "decision_point": self.decision_point.value,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> InsufficientData:
        """Recreate an explicit insufficient-data result from serialized data."""
        _require_status(value, ResultStatus.INSUFFICIENT_DATA)
        return cls(
            probability_type=ProbabilityType(value["probability_type"]),
            outcome=value["outcome"],
            reason=value["reason"],
            data_source=value["data_source"],
            config_version=value["config_version"],
            decision_point=DecisionPoint(value["decision_point"]),
        )


ProbabilityCapabilityResult: TypeAlias = ProbabilityResult | InsufficientData


def _require_probability(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number between 0 and 1")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be a finite number between 0 and 1")


def _require_config_version(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("config_version must be a non-negative integer")
    if value < 0:
        raise ValueError("config_version must be a non-negative integer")


def _require_non_empty_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_enum(value: object, enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise TypeError(f"{field_name} must be a {enum_type.__name__}")


def _require_status(value: dict[str, Any], expected: ResultStatus) -> None:
    if value.get("status") != expected.value:
        raise ValueError(f"serialized result status must be {expected.value!r}")
