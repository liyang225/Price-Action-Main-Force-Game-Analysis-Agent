from __future__ import annotations

from types import MappingProxyType

from src.probability.t1_gate import (
    ExecutableAction,
    ExecutableActionKind,
    T1GateStatus,
)
from src.reasoning.behavior_forecaster import BehaviorForecast
from src.reasoning.scenario_builder import (
    REQUIRED_SCENARIOS,
    ScenarioInputs,
    ScenarioResponseTreeBuilder,
)


def _forecast() -> BehaviorForecast:
    return BehaviorForecast(
        model_behavior="拉升",
        rejected_model_behavior=None,
        key_evidence=("趋势与资金流自洽",),
        probabilities=MappingProxyType({"拉升": 0.6, "震仓": 0.4}),
        prior_weight=1.0,
        disclaimer="专家先验推演，非统计估计",
        routing_config_version=1,
        evidence_trace=(),
    )


def test_tree_covers_three_scenarios_and_all_probability_classes() -> None:
    inputs = {
        scenario: ScenarioInputs(
            behavior_forecasts={"主力": _forecast()},
            opening_distribution={"高开": 0.3, "平开": 0.5, "低开": 0.2},
            first_passage={"target_first": 0.55, "stop_first": 0.25, "neither": 0.2},
            gate_status=T1GateStatus.PASSED,
            executable_actions=(
                ExecutableAction(ExecutableActionKind.PLAN_BUY_NEXT_TRADING_DAY, None),
            ),
            gate_reason="configured probability policy passed",
        )
        for scenario in REQUIRED_SCENARIOS
    }

    tree = ScenarioResponseTreeBuilder().build(inputs)

    assert tuple(branch.name for branch in tree.branches) == REQUIRED_SCENARIOS
    assert all(branch.a_class["主力"].prior_weight == 1.0 for branch in tree.branches)
    assert all(branch.b_class["高开"] == 0.3 for branch in tree.branches)
    assert all(branch.c_class["target_first"] == 0.55 for branch in tree.branches)
    assert all(branch.action_advice for branch in tree.branches)
    assert tree.is_single_path_price_forecast is False


def test_insufficient_data_cannot_be_overridden_by_text_advice() -> None:
    inputs = {
        scenario: ScenarioInputs(
            behavior_forecasts={"主力": _forecast()},
            opening_distribution=None,
            first_passage=None,
            gate_status=T1GateStatus.INSUFFICIENT_DATA,
            executable_actions=(
                ExecutableAction(ExecutableActionKind.SELL_ELIGIBLE_THIS_AFTERNOON, 100),
                ExecutableAction(ExecutableActionKind.PLAN_BUY_THIS_AFTERNOON, None),
            ),
            gate_reason="required B/C probability data is insufficient",
        )
        for scenario in REQUIRED_SCENARIOS
    }

    tree = ScenarioResponseTreeBuilder().build(inputs)

    assert all(branch.status == "insufficient_data" for branch in tree.branches)
    assert all("禁止新增买入" in branch.action_advice for branch in tree.branches)
    assert all(
        tuple(action.kind for action in branch.executable_actions)
        == (ExecutableActionKind.SELL_ELIGIBLE_THIS_AFTERNOON,)
        for branch in tree.branches
    )


def test_builder_rejects_a_missing_scenario() -> None:
    inputs = {
        "超预期强": ScenarioInputs(
            behavior_forecasts={"主力": _forecast()},
            opening_distribution=None,
            first_passage=None,
            gate_status=T1GateStatus.INSUFFICIENT_DATA,
            executable_actions=(),
            gate_reason="missing",
        )
    }

    try:
        ScenarioResponseTreeBuilder().build(inputs)
    except ValueError as error:
        assert "三种情景" in str(error)
    else:
        raise AssertionError("missing scenarios must be rejected")
