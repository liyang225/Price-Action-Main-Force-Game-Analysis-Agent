"""Public behavior tests for the programmatic T+1 gate."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import pytest

from src.probability import (
    DEFAULT_T1_GATE_CONFIG_PATH,
    DecisionPoint,
    ExecutableAction,
    ExecutableActionKind,
    HoldingLot,
    InsufficientData,
    ProbabilityResult,
    ProbabilityType,
    T1FirstPassageEstimate,
    T1GateCalculator,
    T1GateConfig,
    T1GateRequest,
    T1GateStatus,
    load_t1_gate_config,
)


def _gate_config(**changes: object) -> T1GateConfig:
    values: dict[str, object] = {
        "minimum_target_first_probability": 0.5,
        "maximum_stop_first_probability": 0.5,
        "maximum_adverse_opening_probability": 0.5,
        "favorable_opening_outcomes": frozenset({"gap_up"}),
        "neutral_opening_outcomes": frozenset({"near_reference"}),
        "adverse_opening_outcomes": frozenset({"gap_down"}),
        "require_target_over_non_target": True,
        "require_non_adverse_over_adverse": True,
        "config_version": 1,
    }
    values.update(changes)
    return T1GateConfig(**values)


def _probability(
    probability_type: ProbabilityType,
    outcome: str,
    probability: float,
    decision_point: DecisionPoint,
    data_source: str,
) -> ProbabilityResult:
    return ProbabilityResult(
        probability_type=probability_type,
        outcome=outcome,
        probability=probability,
        prior_weight=0.0,
        config_version=7,
        decision_point=decision_point,
        data_source=data_source,
    )


def _opening_distribution(
    decision_point: DecisionPoint,
    *,
    gap_down: float = 0.2,
    near_reference: float = 0.3,
    gap_up: float = 0.5,
) -> tuple[ProbabilityResult, ...]:
    kind = (
        "intraday_next_bar"
        if decision_point is DecisionPoint.MIDDAY
        else "overnight_next_bar"
    )
    source = f"historical_ohlcv:{kind}"
    return (
        _probability(
            ProbabilityType.OPENING_RANGE,
            "gap_down",
            gap_down,
            decision_point,
            source,
        ),
        _probability(
            ProbabilityType.OPENING_RANGE,
            "near_reference",
            near_reference,
            decision_point,
            source,
        ),
        _probability(
            ProbabilityType.OPENING_RANGE,
            "gap_up",
            gap_up,
            decision_point,
            source,
        ),
    )


def _first_passage(
    decision_point: DecisionPoint,
    *,
    target_first: float = 0.6,
    stop_first: float = 0.2,
) -> T1FirstPassageEstimate:
    kind = (
        "intraday_next_bar"
        if decision_point is DecisionPoint.MIDDAY
        else "overnight_next_bar"
    )
    source = f"historical_ohlcv_day_block_bootstrap:{kind}"
    return T1FirstPassageEstimate(
        target_first=_probability(
            ProbabilityType.T1_FIRST_PASSAGE,
            "target_reached_before_stop_loss",
            target_first,
            decision_point,
            source,
        ),
        stop_first=_probability(
            ProbabilityType.T1_FIRST_PASSAGE,
            "stop_loss_reached_before_target",
            stop_first,
            decision_point,
            source,
        ),
        sample_count=20,
        condition_dimensions=("volatility_quantile", "turnover_quantile"),
        dropped_dimensions=(),
    )


def _insufficient(
    probability_type: ProbabilityType,
    outcome: str,
    decision_point: DecisionPoint,
    data_source: str,
) -> InsufficientData:
    return InsufficientData(
        probability_type=probability_type,
        outcome=outcome,
        reason="historical sample is below the configured threshold",
        data_source=data_source,
        config_version=7,
        decision_point=decision_point,
    )


def _insufficient_first_passage(
    decision_point: DecisionPoint,
) -> T1FirstPassageEstimate:
    kind = (
        "intraday_next_bar"
        if decision_point is DecisionPoint.MIDDAY
        else "overnight_next_bar"
    )
    source = f"historical_ohlcv_day_block_bootstrap:{kind}"
    return T1FirstPassageEstimate(
        target_first=_insufficient(
            ProbabilityType.T1_FIRST_PASSAGE,
            "target_reached_before_stop_loss",
            decision_point,
            source,
        ),
        stop_first=_insufficient(
            ProbabilityType.T1_FIRST_PASSAGE,
            "stop_loss_reached_before_target",
            decision_point,
            source,
        ),
        sample_count=2,
        condition_dimensions=("volatility_quantile",),
        dropped_dimensions=("turnover_quantile",),
    )


def test_midday_passes_and_exposes_only_currently_executable_actions() -> None:
    calculator = T1GateCalculator(_gate_config())
    request = T1GateRequest(
        trading_date=date(2026, 8, 11),
        decision_point=DecisionPoint.MIDDAY,
        holdings=(
            HoldingLot(datetime(2026, 8, 10, 14, 0), 200),
            HoldingLot(datetime(2026, 8, 11, 10, 0), 100),
        ),
        opening_distribution=_opening_distribution(DecisionPoint.MIDDAY),
        first_passage=_first_passage(DecisionPoint.MIDDAY),
    )

    result = calculator.evaluate(request)

    assert result.status is T1GateStatus.PASSED
    assert result.gate_passed is True
    assert result.model_override_allowed is False
    assert tuple(action.kind for action in result.executable_actions) == (
        ExecutableActionKind.SELL_ELIGIBLE_THIS_AFTERNOON,
        ExecutableActionKind.PLAN_BUY_THIS_AFTERNOON,
    )
    assert result.executable_actions[0].quantity == 200
    assert result.executable_actions[1].quantity is None
    assert result.to_dict()["executable_actions"] == [
        {
            "kind": "sell_eligible_this_afternoon",
            "quantity": 200,
        },
        {
            "kind": "plan_buy_this_afternoon",
            "quantity": None,
        },
    ]


def test_opening_classification_is_explicitly_configurable() -> None:
    calculator = T1GateCalculator(
        _gate_config(
            favorable_opening_outcomes=frozenset({"strong"}),
            neutral_opening_outcomes=frozenset({"flat"}),
            adverse_opening_outcomes=frozenset({"weak"}),
        )
    )
    opening_distribution = tuple(
        _probability(
            ProbabilityType.OPENING_RANGE,
            outcome,
            probability,
            DecisionPoint.MIDDAY,
            "historical_ohlcv:intraday_next_bar",
        )
        for outcome, probability in (
            ("weak", 0.2),
            ("flat", 0.3),
            ("strong", 0.5),
        )
    )

    result = calculator.evaluate(
        T1GateRequest(
            trading_date=date(2026, 8, 11),
            decision_point=DecisionPoint.MIDDAY,
            holdings=(),
            opening_distribution=opening_distribution,
            first_passage=_first_passage(DecisionPoint.MIDDAY),
        )
    )

    assert result.status is T1GateStatus.PASSED
    assert result.adverse_opening_probability == 0.2
    assert result.neutral_opening_probability == 0.3
    assert result.favorable_opening_probability == 0.5


def test_close_plans_next_day_actions_for_all_current_holdings() -> None:
    result = T1GateCalculator(_gate_config()).evaluate(
        T1GateRequest(
            trading_date=date(2026, 8, 11),
            decision_point=DecisionPoint.CLOSE,
            holdings=(
                HoldingLot(datetime(2026, 8, 10, 14, 0), 200),
                HoldingLot(datetime(2026, 8, 11, 13, 0), 100),
            ),
            opening_distribution=_opening_distribution(DecisionPoint.CLOSE),
            first_passage=_first_passage(DecisionPoint.CLOSE),
        )
    )

    assert result.status is T1GateStatus.PASSED
    assert tuple(action.kind for action in result.executable_actions) == (
        ExecutableActionKind.PLAN_SELL_NEXT_TRADING_DAY,
        ExecutableActionKind.PLAN_BUY_NEXT_TRADING_DAY,
    )
    assert result.executable_actions[0].quantity == 300


@pytest.mark.parametrize("missing_probability", ["B", "C"])
def test_any_required_probability_shortage_blocks_new_buys(
    missing_probability: str,
) -> None:
    point = DecisionPoint.MIDDAY
    opening_distribution = _opening_distribution(point)
    first_passage = _first_passage(point)
    if missing_probability == "B":
        opening_distribution = _insufficient(
            ProbabilityType.OPENING_RANGE,
            "intraday_next_bar_distribution",
            point,
            "historical_ohlcv:intraday_next_bar",
        )
    else:
        first_passage = _insufficient_first_passage(point)

    result = T1GateCalculator(_gate_config()).evaluate(
        T1GateRequest(
            trading_date=date(2026, 8, 11),
            decision_point=point,
            holdings=(HoldingLot(datetime(2026, 8, 10, 14, 0), 200),),
            opening_distribution=opening_distribution,
            first_passage=first_passage,
        )
    )

    assert result.status is T1GateStatus.INSUFFICIENT_DATA
    assert result.gate_passed is False
    assert result.model_override_allowed is False
    assert tuple(action.kind for action in result.executable_actions) == (
        ExecutableActionKind.SELL_ELIGIBLE_THIS_AFTERNOON,
    )
    assert result.target_first_probability is None


def test_available_but_adverse_probabilities_block_buy_not_risk_reduction() -> None:
    result = T1GateCalculator(_gate_config()).evaluate(
        T1GateRequest(
            trading_date=date(2026, 8, 11),
            decision_point=DecisionPoint.MIDDAY,
            holdings=(HoldingLot(datetime(2026, 8, 10, 14, 0), 200),),
            opening_distribution=_opening_distribution(
                DecisionPoint.MIDDAY,
                gap_down=0.6,
                near_reference=0.2,
                gap_up=0.2,
            ),
            first_passage=_first_passage(DecisionPoint.MIDDAY),
        )
    )

    assert result.status is T1GateStatus.BLOCKED
    assert result.gate_passed is False
    assert tuple(action.kind for action in result.executable_actions) == (
        ExecutableActionKind.SELL_ELIGIBLE_THIS_AFTERNOON,
    )
    assert "adverse opening" in result.reason


def test_injected_threshold_can_make_an_otherwise_majority_result_fail() -> None:
    result = T1GateCalculator(
        _gate_config(minimum_target_first_probability=0.7)
    ).evaluate(
        T1GateRequest(
            trading_date=date(2026, 8, 11),
            decision_point=DecisionPoint.MIDDAY,
            holdings=(),
            opening_distribution=_opening_distribution(DecisionPoint.MIDDAY),
            first_passage=_first_passage(
                DecisionPoint.MIDDAY,
                target_first=0.6,
                stop_first=0.2,
            ),
        )
    )

    assert result.status is T1GateStatus.BLOCKED
    assert result.executable_actions == ()
    assert "configured minimum" in result.reason


def test_probability_ties_are_blocked_conservatively() -> None:
    result = T1GateCalculator(_gate_config()).evaluate(
        T1GateRequest(
            trading_date=date(2026, 8, 11),
            decision_point=DecisionPoint.MIDDAY,
            holdings=(),
            opening_distribution=_opening_distribution(
                DecisionPoint.MIDDAY,
                gap_down=0.5,
                near_reference=0.2,
                gap_up=0.3,
            ),
            first_passage=_first_passage(
                DecisionPoint.MIDDAY,
                target_first=0.5,
                stop_first=0.5,
            ),
        )
    )

    assert result.status is T1GateStatus.BLOCKED
    assert result.executable_actions == ()


def test_target_first_must_dominate_stop_and_neither_combined() -> None:
    result = T1GateCalculator(_gate_config()).evaluate(
        T1GateRequest(
            trading_date=date(2026, 8, 11),
            decision_point=DecisionPoint.MIDDAY,
            holdings=(),
            opening_distribution=_opening_distribution(DecisionPoint.MIDDAY),
            first_passage=_first_passage(
                DecisionPoint.MIDDAY,
                target_first=0.5,
                stop_first=0.2,
            ),
        )
    )

    assert result.status is T1GateStatus.BLOCKED
    assert "non-target outcomes" in result.reason


def test_same_day_midday_holding_is_not_sellable() -> None:
    result = T1GateCalculator(_gate_config()).evaluate(
        T1GateRequest(
            trading_date=date(2026, 8, 11),
            decision_point=DecisionPoint.MIDDAY,
            holdings=(HoldingLot(datetime(2026, 8, 11, 10, 0), 100),),
            opening_distribution=_opening_distribution(
                DecisionPoint.MIDDAY,
                gap_down=0.6,
                near_reference=0.2,
                gap_up=0.2,
            ),
            first_passage=_first_passage(DecisionPoint.MIDDAY),
        )
    )

    assert result.status is T1GateStatus.BLOCKED
    assert result.executable_actions == ()


def test_wrong_physical_distribution_for_decision_point_is_rejected() -> None:
    opening_distribution = tuple(
        replace(item, data_source="historical_ohlcv:overnight_next_bar")
        for item in _opening_distribution(DecisionPoint.MIDDAY)
    )

    with pytest.raises(ValueError, match="decision-point semantics"):
        T1GateCalculator(_gate_config()).evaluate(
            T1GateRequest(
                trading_date=date(2026, 8, 11),
                decision_point=DecisionPoint.MIDDAY,
                holdings=(),
                opening_distribution=opening_distribution,
                first_passage=_first_passage(DecisionPoint.MIDDAY),
            )
        )


def test_insufficient_result_from_wrong_distribution_is_rejected() -> None:
    with pytest.raises(ValueError, match="decision-point semantics"):
        T1GateCalculator(_gate_config()).evaluate(
            T1GateRequest(
                trading_date=date(2026, 8, 11),
                decision_point=DecisionPoint.MIDDAY,
                holdings=(),
                opening_distribution=_insufficient(
                    ProbabilityType.OPENING_RANGE,
                    "overnight_next_bar_distribution",
                    DecisionPoint.MIDDAY,
                    "historical_ohlcv:overnight_next_bar",
                ),
                first_passage=_first_passage(DecisionPoint.MIDDAY),
            )
        )


def test_holding_acquired_after_the_decision_point_is_rejected() -> None:
    with pytest.raises(ValueError, match="after the decision point"):
        T1GateCalculator(_gate_config()).evaluate(
            T1GateRequest(
                trading_date=date(2026, 8, 11),
                decision_point=DecisionPoint.MIDDAY,
                holdings=(HoldingLot(datetime(2026, 8, 11, 13, 0), 100),),
                opening_distribution=_opening_distribution(DecisionPoint.MIDDAY),
                first_passage=_first_passage(DecisionPoint.MIDDAY),
            )
        )


def test_opening_outcome_groups_must_be_disjoint() -> None:
    with pytest.raises(ValueError, match="disjoint"):
        _gate_config(
            favorable_opening_outcomes=frozenset({"gap_up"}),
            neutral_opening_outcomes=frozenset({"gap_up"}),
        )


def test_repository_config_loads_the_versioned_majority_policy() -> None:
    loaded = load_t1_gate_config()

    assert DEFAULT_T1_GATE_CONFIG_PATH.name == "t1_gate.yaml"
    assert loaded == _gate_config()


def test_config_loader_rejects_unknown_policy_keys(tmp_path) -> None:
    invalid = tmp_path / "t1_gate.yaml"
    invalid.write_text(
        DEFAULT_T1_GATE_CONFIG_PATH.read_text(encoding="utf-8").replace(
            "version: 1", "version: 1\nunknown_policy: true"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown_policy"):
        load_t1_gate_config(invalid)


def test_public_action_contract_rejects_kind_quantity_contradictions() -> None:
    with pytest.raises(ValueError, match="sell actions"):
        ExecutableAction(
            ExecutableActionKind.SELL_ELIGIBLE_THIS_AFTERNOON,
            None,
        )
    with pytest.raises(ValueError, match="buy plans"):
        ExecutableAction(
            ExecutableActionKind.PLAN_BUY_THIS_AFTERNOON,
            100,
        )
