"""Bounded end-to-end orchestration for one stock/sector reasoning run."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from src.reasoning.behavior_forecaster import BehaviorForecastRequest, BehaviorForecaster
from src.reasoning.participant_classifier import ParticipantClassifier
from src.reasoning.scenario_builder import (
    REQUIRED_SCENARIOS,
    ScenarioInputs,
    ScenarioResponseTree,
    ScenarioResponseTreeBuilder,
)
from src.reasoning.scenario_advice_generator import ScenarioAdviceGenerator
from src.signals.dragon_tiger import DragonTigerSignal


MODEL_TIMEOUT_BUDGET_SECONDS = 90.0


@dataclass(frozen=True, slots=True)
class ReasoningPipelineRequest:
    cycle_position: str
    policy_environment: str
    materials: Mapping[str, Any]
    game_signals: Mapping[str, Any]
    sector_belief: Mapping[str, float]
    prior_weight: float
    scenario_probabilities_and_gates: Mapping[str, ScenarioInputs]
    dragon_tiger: DragonTigerSignal | None = None


class ReasoningPipeline:
    """Use exactly two bounded model calls, then assemble all branches in code."""

    def __init__(
        self,
        classifier: ParticipantClassifier,
        forecaster: BehaviorForecaster,
        builder: ScenarioResponseTreeBuilder | None = None,
        advice_generator: ScenarioAdviceGenerator | None = None,
    ) -> None:
        self._classifier = classifier
        self._forecaster = forecaster
        self._builder = builder or ScenarioResponseTreeBuilder()
        self._advice_generator = advice_generator

    def run(self, request: ReasoningPipelineRequest) -> ScenarioResponseTree:
        classification = self._classifier.classify(
            {"materials": request.materials, "game_signals": request.game_signals}
        )
        if classification.status != "ok" or classification.participant is None:
            raise ValueError("参与者证据不足，无法启动行为推演")
        forecast = self._forecaster.forecast(
            BehaviorForecastRequest(
                cycle_position=request.cycle_position,
                participant=classification.participant,
                policy_environment=request.policy_environment,
                materials=request.materials,
                game_signals=request.game_signals,
                sector_belief=request.sector_belief,
                prior_weight=request.prior_weight,
                possible_behaviors=frozenset(classification.behavior_candidates),
                dragon_tiger=request.dragon_tiger,
            )
        )
        if set(request.scenario_probabilities_and_gates) != set(REQUIRED_SCENARIOS):
            raise ValueError("完整推演需要三种情景的 B/C 概率与闸门")
        scenarios = {
            name: ScenarioInputs(
                behavior_forecasts={classification.participant: forecast},
                opening_distribution=inputs.opening_distribution,
                first_passage=inputs.first_passage,
                gate_status=inputs.gate_status,
                executable_actions=inputs.executable_actions,
                gate_reason=inputs.gate_reason,
            )
            for name, inputs in request.scenario_probabilities_and_gates.items()
        }
        tree = self._builder.build(scenarios)
        branches = tree.branches
        if self._advice_generator is not None:
            advice = self._advice_generator.generate(
                request.materials,
                cycle_position=request.cycle_position,
                policy_environment=request.policy_environment,
                participant=classification.participant,
                model_behavior=forecast.model_behavior,
                key_evidence=forecast.key_evidence,
                branches=[_branch_summary(branch) for branch in branches],
            )
            branches = tuple(
                replace(branch, action_advice=advice.get(branch.name, branch.action_advice))
                for branch in branches
            )
        return replace(
            tree,
            branches=branches,
            analysis_metadata={
                "participant_analysis": {
                    "status": classification.status,
                    "participant": classification.participant,
                    "behavior_candidates": list(classification.behavior_candidates),
                    "key_evidence": list(classification.key_evidence),
                    "contra_evidence": list(classification.contra_evidence),
                }
            },
        )


def _branch_summary(branch: Any) -> dict[str, Any]:
    return {
        "name": branch.name,
        "status": branch.status,
        "opening_probability": (
            dict(branch.b_class) if branch.b_class is not None else None
        ),
        "first_passage": dict(branch.c_class) if branch.c_class is not None else None,
        "gate_reason": branch.gate_reason,
    }


__all__ = ["MODEL_TIMEOUT_BUDGET_SECONDS", "ReasoningPipeline", "ReasoningPipelineRequest"]
