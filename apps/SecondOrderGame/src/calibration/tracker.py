"""Auditable, offline calibration tracking for probability forecasts.

The tracker persists immutable probability snapshots and pairs each snapshot
with at most one subsequently observed result.  Evaluation is deliberately a
read-only operation: it never updates a forecast, a prior, or HMM state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from pathlib import Path
import sqlite3
from typing import TypeAlias

from src.probability.models import (
    DecisionPoint,
    ProbabilityResult,
    ProbabilityType,
    ResultStatus,
)


DEFAULT_MINIMUM_SAMPLE_COUNT = 30


class CalibrationTrackerError(RuntimeError):
    """Base error for invalid persisted calibration operations."""


class DuplicatePredictionError(CalibrationTrackerError):
    """A prediction identifier was reused for a different snapshot."""


class DuplicateResultError(CalibrationTrackerError):
    """A resolved prediction was given a conflicting actual result."""


class UnknownPredictionError(CalibrationTrackerError):
    """An actual result referred to a prediction that was never recorded."""


class PriorAdjustmentDirection(str, Enum):
    """Direction for a later human review of the frozen prior values."""

    INCREASE = "increase"
    DECREASE = "decrease"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class CalibrationScope:
    """One comparable forecast population, isolated by configuration version."""

    probability_type: ProbabilityType
    outcome: str
    decision_point: DecisionPoint
    config_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.probability_type, ProbabilityType):
            raise TypeError("probability_type must be a ProbabilityType")
        _require_non_empty_text(self.outcome, "outcome")
        if not isinstance(self.decision_point, DecisionPoint):
            raise TypeError("decision_point must be a DecisionPoint")
        _require_config_version(self.config_version)

    def to_dict(self) -> dict[str, str | int]:
        return {
            "probability_type": self.probability_type.value,
            "outcome": self.outcome,
            "decision_point": self.decision_point.value,
            "config_version": self.config_version,
        }


@dataclass(frozen=True, slots=True)
class CalibrationPrediction:
    """A persisted probability snapshot and its optional observed result."""

    prediction_id: str
    prediction: ProbabilityResult
    recorded_at: datetime
    actual_result: bool | str | None = None
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty_text(self.prediction_id, "prediction_id")
        if not isinstance(self.prediction, ProbabilityResult):
            raise TypeError("prediction must be an available ProbabilityResult")
        _require_aware_datetime(self.recorded_at, "recorded_at")
        if self.actual_result is None:
            if self.resolved_at is not None:
                raise ValueError("unresolved predictions cannot have resolved_at")
        else:
            _validate_actual_result(self.actual_result)
            if self.resolved_at is None:
                raise ValueError("resolved predictions must have resolved_at")
            _require_aware_datetime(self.resolved_at, "resolved_at")

    @property
    def probability_type(self) -> ProbabilityType:
        return self.prediction.probability_type

    @property
    def outcome(self) -> str:
        return self.prediction.outcome

    @property
    def probability(self) -> float:
        return self.prediction.probability

    @property
    def prior_weight(self) -> float:
        return self.prediction.prior_weight

    @property
    def config_version(self) -> int:
        return self.prediction.config_version

    @property
    def decision_point(self) -> DecisionPoint:
        return self.prediction.decision_point

    def to_dict(self) -> dict[str, object]:
        return {
            "prediction_id": self.prediction_id,
            "prediction": self.prediction.to_dict(),
            "recorded_at": self.recorded_at.isoformat(),
            "actual_result": self.actual_result,
            "resolved_at": (
                None if self.resolved_at is None else self.resolved_at.isoformat()
            ),
        }


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """A Brier score that met the configured minimum sample count."""

    scope: CalibrationScope
    sample_count: int
    brier_score: float
    mean_predicted_probability: float
    observed_frequency: float
    mean_prior_weight: float
    prior_adjustment_direction: PriorAdjustmentDirection
    status: ResultStatus = field(default=ResultStatus.AVAILABLE, init=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            **self.scope.to_dict(),
            "sample_count": self.sample_count,
            "brier_score": self.brier_score,
            "mean_predicted_probability": self.mean_predicted_probability,
            "observed_frequency": self.observed_frequency,
            "mean_prior_weight": self.mean_prior_weight,
            "prior_adjustment_direction": self.prior_adjustment_direction.value,
        }


@dataclass(frozen=True, slots=True)
class InsufficientCalibration:
    """An explicit refusal to publish a score from too few resolved forecasts."""

    scope: CalibrationScope
    sample_count: int
    minimum_sample_count: int
    reason: str
    status: ResultStatus = field(default=ResultStatus.INSUFFICIENT_DATA, init=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            **self.scope.to_dict(),
            "sample_count": self.sample_count,
            "minimum_sample_count": self.minimum_sample_count,
            "reason": self.reason,
        }


CalibrationEvaluation: TypeAlias = CalibrationReport | InsufficientCalibration


class CalibrationTracker:
    """Append-only SQLite ledger plus offline, version-scoped evaluation."""

    def __init__(
        self,
        database: str | Path | sqlite3.Connection,
        *,
        min_samples: int = DEFAULT_MINIMUM_SAMPLE_COUNT,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        _require_positive_integer(min_samples, "min_samples")
        self._minimum_sample_count = min_samples
        self._clock = clock or _utc_now
        self._owns_connection = not isinstance(database, sqlite3.Connection)
        if self._owns_connection:
            database_path = str(database) if str(database) == ":memory:" else Path(database)
            if isinstance(database_path, Path):
                database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(database_path)
        else:
            self._connection = database
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize()

    @property
    def minimum_sample_count(self) -> int:
        return self._minimum_sample_count

    def close(self) -> None:
        if self._owns_connection:
            self._connection.close()

    def __enter__(self) -> CalibrationTracker:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def record_prediction(
        self,
        prediction_id: str,
        prediction: ProbabilityResult,
        *,
        recorded_at: datetime | None = None,
    ) -> CalibrationPrediction:
        """Persist an immutable snapshot without consulting current config state."""

        _require_non_empty_text(prediction_id, "prediction_id")
        if not isinstance(prediction, ProbabilityResult):
            raise TypeError("prediction must be an available ProbabilityResult")
        timestamp = self._timestamp(recorded_at)
        existing = self.get_prediction(prediction_id)
        if existing is not None:
            if existing.prediction == prediction:
                return existing
            raise DuplicatePredictionError(
                f"prediction {prediction_id!r} already has a different snapshot"
            )

        self._connection.execute(
            """
            INSERT INTO calibration_predictions (
                prediction_id,
                probability_type,
                outcome,
                probability,
                prior_weight,
                config_version,
                decision_point,
                data_source,
                recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prediction_id,
                prediction.probability_type.value,
                prediction.outcome,
                prediction.probability,
                prediction.prior_weight,
                prediction.config_version,
                prediction.decision_point.value,
                prediction.data_source,
                timestamp.isoformat(),
            ),
        )
        self._connection.commit()
        saved = self.get_prediction(prediction_id)
        if saved is None:  # pragma: no cover - defensive against SQLite corruption
            raise CalibrationTrackerError("prediction insert could not be read back")
        return saved

    def record_actual_result(
        self,
        prediction_id: str,
        actual_result: bool | int | str,
        *,
        resolved_at: datetime | None = None,
    ) -> bool:
        """Pair one result; exact retries are idempotent, conflicts are rejected."""

        _require_non_empty_text(prediction_id, "prediction_id")
        normalized_result = _normalize_actual_result(actual_result)
        existing = self.get_prediction(prediction_id)
        if existing is None:
            raise UnknownPredictionError(f"unknown prediction {prediction_id!r}")
        if existing.actual_result is not None:
            if existing.actual_result == normalized_result:
                return False
            raise DuplicateResultError(
                f"prediction {prediction_id!r} already has a different result"
            )

        actual_kind, actual_boolean, actual_outcome = _serialize_actual_result(
            normalized_result
        )
        timestamp = self._timestamp(resolved_at)
        cursor = self._connection.execute(
            """
            UPDATE calibration_predictions
            SET actual_kind = ?,
                actual_boolean = ?,
                actual_outcome = ?,
                resolved_at = ?
            WHERE prediction_id = ? AND actual_kind IS NULL
            """,
            (
                actual_kind,
                actual_boolean,
                actual_outcome,
                timestamp.isoformat(),
                prediction_id,
            ),
        )
        self._connection.commit()
        if cursor.rowcount != 1:  # pragma: no cover - protects concurrent writers
            raise DuplicateResultError(
                f"prediction {prediction_id!r} was resolved concurrently"
            )
        return True

    def get_prediction(self, prediction_id: str) -> CalibrationPrediction | None:
        _require_non_empty_text(prediction_id, "prediction_id")
        row = self._connection.execute(
            """
            SELECT
                prediction_id,
                probability_type,
                outcome,
                probability,
                prior_weight,
                config_version,
                decision_point,
                data_source,
                recorded_at,
                actual_kind,
                actual_boolean,
                actual_outcome,
                resolved_at
            FROM calibration_predictions
            WHERE prediction_id = ?
            """,
            (prediction_id,),
        ).fetchone()
        return None if row is None else _prediction_from_row(row)

    def prediction_count(self) -> int:
        return _count(self._connection, "1")

    def resolved_count(self) -> int:
        return _count(self._connection, "actual_kind IS NOT NULL")

    def unresolved_count(self) -> int:
        return _count(self._connection, "actual_kind IS NULL")

    def evaluate(
        self,
        scope: CalibrationScope,
    ) -> CalibrationEvaluation:
        """Calculate a score from one fixed-version scope without changing state."""

        if not isinstance(scope, CalibrationScope):
            raise TypeError("scope must be a CalibrationScope")
        rows = self._connection.execute(
            """
            SELECT probability, prior_weight, outcome,
                   actual_kind, actual_boolean, actual_outcome
            FROM calibration_predictions
            WHERE probability_type = ?
              AND outcome = ?
              AND decision_point = ?
              AND config_version = ?
              AND actual_kind IS NOT NULL
            ORDER BY recorded_at, prediction_id
            """,
            (
                scope.probability_type.value,
                scope.outcome,
                scope.decision_point.value,
                scope.config_version,
            ),
        ).fetchall()
        sample_count = len(rows)
        if sample_count < self._minimum_sample_count:
            return InsufficientCalibration(
                scope=scope,
                sample_count=sample_count,
                minimum_sample_count=self._minimum_sample_count,
                reason=(
                    f"{sample_count} resolved predictions available; "
                    f"{self._minimum_sample_count} required"
                ),
            )

        probabilities = [float(row["probability"]) for row in rows]
        actual_events = [_actual_event(row) for row in rows]
        brier_score = math.fsum(
            (probability - actual) ** 2
            for probability, actual in zip(probabilities, actual_events, strict=True)
        ) / sample_count
        mean_probability = math.fsum(probabilities) / sample_count
        observed_frequency = math.fsum(actual_events) / sample_count
        mean_prior_weight = (
            math.fsum(float(row["prior_weight"]) for row in rows) / sample_count
        )
        return CalibrationReport(
            scope=scope,
            sample_count=sample_count,
            brier_score=brier_score,
            mean_predicted_probability=mean_probability,
            observed_frequency=observed_frequency,
            mean_prior_weight=mean_prior_weight,
            prior_adjustment_direction=_direction(
                mean_probability, observed_frequency
            ),
        )

    def evaluate_all(self) -> tuple[CalibrationEvaluation, ...]:
        """Evaluate every resolved scope separately; versions are never pooled."""

        rows = self._connection.execute(
            """
            SELECT DISTINCT probability_type, outcome, decision_point, config_version
            FROM calibration_predictions
            WHERE actual_kind IS NOT NULL
            ORDER BY probability_type, outcome, decision_point, config_version
            """
        ).fetchall()
        return tuple(
            self.evaluate(
                CalibrationScope(
                    probability_type=ProbabilityType(row["probability_type"]),
                    outcome=row["outcome"],
                    decision_point=DecisionPoint(row["decision_point"]),
                    config_version=int(row["config_version"]),
                )
            )
            for row in rows
        )

    def _timestamp(self, supplied: datetime | None) -> datetime:
        value = self._clock() if supplied is None else supplied
        _require_aware_datetime(value, "timestamp")
        return value

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS calibration_predictions (
                prediction_id TEXT PRIMARY KEY,
                probability_type TEXT NOT NULL CHECK (probability_type IN ('A', 'B', 'C')),
                outcome TEXT NOT NULL,
                probability REAL NOT NULL CHECK (probability >= 0 AND probability <= 1),
                prior_weight REAL NOT NULL CHECK (prior_weight >= 0 AND prior_weight <= 1),
                config_version INTEGER NOT NULL CHECK (config_version >= 0),
                decision_point TEXT NOT NULL CHECK (decision_point IN ('午盘', '收盘')),
                data_source TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                actual_kind TEXT CHECK (actual_kind IN ('boolean', 'outcome')),
                actual_boolean INTEGER CHECK (actual_boolean IN (0, 1)),
                actual_outcome TEXT,
                resolved_at TEXT,
                CHECK (
                    (actual_kind IS NULL AND actual_boolean IS NULL
                     AND actual_outcome IS NULL AND resolved_at IS NULL)
                    OR
                    (actual_kind = 'boolean' AND actual_boolean IS NOT NULL
                     AND actual_outcome IS NULL AND resolved_at IS NOT NULL)
                    OR
                    (actual_kind = 'outcome' AND actual_boolean IS NULL
                     AND actual_outcome IS NOT NULL AND resolved_at IS NOT NULL)
                )
            );

            CREATE INDEX IF NOT EXISTS calibration_scope_index
            ON calibration_predictions (
                probability_type, outcome, decision_point, config_version, actual_kind
            );
            """
        )
        self._connection.commit()


def _prediction_from_row(row: sqlite3.Row) -> CalibrationPrediction:
    actual_result = _deserialize_actual_result(row)
    resolved_at = (
        None
        if row["resolved_at"] is None
        else datetime.fromisoformat(row["resolved_at"])
    )
    return CalibrationPrediction(
        prediction_id=row["prediction_id"],
        prediction=ProbabilityResult(
            probability_type=ProbabilityType(row["probability_type"]),
            outcome=row["outcome"],
            probability=float(row["probability"]),
            prior_weight=float(row["prior_weight"]),
            config_version=int(row["config_version"]),
            decision_point=DecisionPoint(row["decision_point"]),
            data_source=row["data_source"],
        ),
        recorded_at=datetime.fromisoformat(row["recorded_at"]),
        actual_result=actual_result,
        resolved_at=resolved_at,
    )


def _normalize_actual_result(value: bool | int | str) -> bool | str:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    _require_non_empty_text(value, "actual_result")
    return value.strip()


def _validate_actual_result(value: object) -> None:
    if isinstance(value, bool):
        return
    _require_non_empty_text(value, "actual_result")


def _serialize_actual_result(value: bool | str) -> tuple[str, int | None, str | None]:
    if isinstance(value, bool):
        return "boolean", int(value), None
    return "outcome", None, value


def _deserialize_actual_result(row: sqlite3.Row) -> bool | str | None:
    if row["actual_kind"] is None:
        return None
    if row["actual_kind"] == "boolean":
        return bool(row["actual_boolean"])
    return str(row["actual_outcome"])


def _actual_event(row: sqlite3.Row) -> float:
    if row["actual_kind"] == "boolean":
        return float(row["actual_boolean"])
    return float(row["actual_outcome"] == row["outcome"])


def _direction(
    mean_predicted_probability: float,
    observed_frequency: float,
) -> PriorAdjustmentDirection:
    if math.isclose(mean_predicted_probability, observed_frequency, abs_tol=1e-12):
        return PriorAdjustmentDirection.HOLD
    if observed_frequency > mean_predicted_probability:
        return PriorAdjustmentDirection.INCREASE
    return PriorAdjustmentDirection.DECREASE


def _count(connection: sqlite3.Connection, condition: str) -> int:
    return int(
        connection.execute(
            f"SELECT COUNT(*) FROM calibration_predictions WHERE {condition}"
        ).fetchone()[0]
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_non_empty_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_config_version(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("config_version must be a non-negative integer")


def _require_positive_integer(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")


def _require_aware_datetime(value: object, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")


__all__ = [
    "CalibrationEvaluation",
    "CalibrationPrediction",
    "CalibrationReport",
    "CalibrationScope",
    "CalibrationTracker",
    "CalibrationTrackerError",
    "DuplicatePredictionError",
    "DuplicateResultError",
    "InsufficientCalibration",
    "PriorAdjustmentDirection",
    "UnknownPredictionError",
]
