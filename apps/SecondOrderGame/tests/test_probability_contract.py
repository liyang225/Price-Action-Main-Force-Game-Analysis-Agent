"""Public contract tests for probability capability results."""

from __future__ import annotations

import json

import pytest

from src.probability import (
    DecisionPoint,
    InsufficientData,
    ProbabilityResult,
    ProbabilityType,
)


def test_valid_probability_result_round_trips_through_json() -> None:
    result = ProbabilityResult(
        probability_type=ProbabilityType.OPENING_RANGE,
        outcome="opens_above_reference",
        probability=0.62,
        prior_weight=0.35,
        config_version=2,
        decision_point=DecisionPoint.CLOSE,
        data_source="historical_ohlcv",
    )

    serialized = result.to_dict()

    assert serialized == {
        "status": "available",
        "probability_type": "B",
        "outcome": "opens_above_reference",
        "probability": 0.62,
        "prior_weight": 0.35,
        "disclaimer": "专家先验推演，非统计估计",
        "config_version": 2,
        "decision_point": "收盘",
        "data_source": "historical_ohlcv",
    }
    assert json.loads(json.dumps(serialized, ensure_ascii=False)) == serialized
    assert ProbabilityResult.from_dict(serialized) == result


def test_probability_result_omits_disclaimer_after_row_exits_prior_threshold() -> None:
    result = ProbabilityResult(
        probability_type=ProbabilityType.T1_FIRST_PASSAGE,
        outcome="target_first",
        probability=0.62,
        prior_weight=0.19,
        config_version=2,
        decision_point=DecisionPoint.CLOSE,
        data_source="historical_ohlcv",
    )

    assert "disclaimer" not in result.to_dict()


def test_insufficient_data_is_a_separate_serializable_result_without_probability() -> None:
    result = InsufficientData(
        probability_type=ProbabilityType.T1_FIRST_PASSAGE,
        outcome="target_reached_before_stop_loss",
        reason="matching historical sample is below the required threshold",
        data_source="historical_ohlcv",
        config_version=2,
        decision_point=DecisionPoint.MIDDAY,
    )

    serialized = result.to_dict()

    assert serialized == {
        "status": "insufficient_data",
        "probability_type": "C",
        "outcome": "target_reached_before_stop_loss",
        "reason": "matching historical sample is below the required threshold",
        "data_source": "historical_ohlcv",
        "config_version": 2,
        "decision_point": "午盘",
    }
    assert "probability" not in serialized
    assert json.loads(json.dumps(serialized, ensure_ascii=False)) == serialized
    assert InsufficientData.from_dict(serialized) == result


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("probability", -0.01),
        ("probability", 1.01),
        ("probability", float("nan")),
        ("prior_weight", -0.01),
        ("prior_weight", 1.01),
        ("prior_weight", float("inf")),
    ],
)
def test_probability_result_rejects_invalid_probability_values(
    field_name: str, invalid_value: float
) -> None:
    values = {
        "probability_type": ProbabilityType.OPENING_RANGE,
        "outcome": "opens_above_reference",
        "probability": 0.62,
        "prior_weight": 0.35,
        "config_version": 2,
        "decision_point": DecisionPoint.CLOSE,
        "data_source": "historical_ohlcv",
    }
    values[field_name] = invalid_value

    with pytest.raises(ValueError, match=field_name):
        ProbabilityResult(**values)
