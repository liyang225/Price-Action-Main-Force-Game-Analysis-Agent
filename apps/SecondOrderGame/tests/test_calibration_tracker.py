"""Public behavior tests for the offline calibration tracker."""

from __future__ import annotations

import pytest

from src.probability import DecisionPoint, ProbabilityResult, ProbabilityType

from src.calibration import (
    CalibrationScope,
    CalibrationTracker,
    DuplicateResultError,
    InsufficientCalibration,
    PriorAdjustmentDirection,
)


def _prediction(
    probability: float,
    *,
    config_version: int = 7,
) -> ProbabilityResult:
    return ProbabilityResult(
        probability_type=ProbabilityType.T1_FIRST_PASSAGE,
        outcome="target_reached_before_stop_loss",
        probability=probability,
        prior_weight=0.25,
        config_version=config_version,
        decision_point=DecisionPoint.CLOSE,
        data_source="historical_ohlcv:overnight_next_bar",
    )


def _scope(config_version: int = 7) -> CalibrationScope:
    return CalibrationScope(
        probability_type=ProbabilityType.T1_FIRST_PASSAGE,
        outcome="target_reached_before_stop_loss",
        decision_point=DecisionPoint.CLOSE,
        config_version=config_version,
    )


def test_prediction_and_actual_result_are_persisted_as_an_auditable_snapshot(
    temp_db,
) -> None:
    tracker = CalibrationTracker(temp_db, min_samples=3)
    prediction = _prediction(0.7)

    saved = tracker.record_prediction("forecast-1", prediction)
    inserted = tracker.record_actual_result("forecast-1", True)
    restored = tracker.get_prediction("forecast-1")

    assert inserted is True
    assert saved.prediction == prediction
    assert restored is not None
    assert restored.prediction == prediction
    assert restored.actual_result is True
    assert tracker.unresolved_count() == 0


def test_duplicate_actual_result_is_idempotent_but_a_conflicting_retry_fails(
    temp_db,
) -> None:
    tracker = CalibrationTracker(temp_db, min_samples=1)
    tracker.record_prediction("forecast-1", _prediction(0.7))

    assert tracker.record_actual_result("forecast-1", True) is True
    assert tracker.record_actual_result("forecast-1", True) is False
    assert tracker.resolved_count() == 1
    with pytest.raises(DuplicateResultError, match="already has a different result"):
        tracker.record_actual_result("forecast-1", False)


def test_brier_score_is_withheld_until_the_minimum_resolved_sample_count(temp_db) -> None:
    tracker = CalibrationTracker(temp_db, min_samples=3)
    tracker.record_prediction("forecast-1", _prediction(0.9))
    tracker.record_actual_result("forecast-1", True)
    tracker.record_prediction("forecast-2", _prediction(0.2))
    tracker.record_actual_result("forecast-2", False)

    evaluation = tracker.evaluate(_scope())

    assert isinstance(evaluation, InsufficientCalibration)
    assert evaluation.sample_count == 2
    assert evaluation.minimum_sample_count == 3
    assert not hasattr(evaluation, "brier_score")


def test_known_brier_score_and_prior_adjustment_direction_after_threshold(temp_db) -> None:
    tracker = CalibrationTracker(temp_db, min_samples=3)
    for prediction_id, probability, actual in (
        ("forecast-1", 0.9, True),
        ("forecast-2", 0.2, False),
        ("forecast-3", 0.6, True),
    ):
        tracker.record_prediction(prediction_id, _prediction(probability))
        tracker.record_actual_result(prediction_id, actual)

    evaluation = tracker.evaluate(_scope())

    assert evaluation.brier_score == pytest.approx(0.07)
    assert evaluation.observed_frequency == pytest.approx(2 / 3)
    assert evaluation.mean_predicted_probability == pytest.approx(1.7 / 3)
    assert evaluation.prior_adjustment_direction is PriorAdjustmentDirection.INCREASE


def test_evaluation_is_isolated_by_the_config_version_bound_to_each_prediction(temp_db) -> None:
    tracker = CalibrationTracker(temp_db, min_samples=2)
    tracker.record_prediction("v7-a", _prediction(0.9, config_version=7))
    tracker.record_actual_result("v7-a", True)
    tracker.record_prediction("v7-b", _prediction(0.9, config_version=7))
    tracker.record_actual_result("v7-b", True)
    tracker.record_prediction("v8-a", _prediction(0.1, config_version=8))
    tracker.record_actual_result("v8-a", False)

    version_seven = tracker.evaluate(_scope(7))
    version_eight = tracker.evaluate(_scope(8))

    assert version_seven.sample_count == 2
    assert version_seven.brier_score == pytest.approx(0.01)
    assert isinstance(version_eight, InsufficientCalibration)
    assert version_eight.sample_count == 1
