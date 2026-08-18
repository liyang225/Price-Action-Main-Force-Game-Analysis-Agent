from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field

from src.hmm_filter import load_config
from src.integration.model_adapter import ModelResponse
from src.reasoning.behavior_forecaster import BehaviorForecaster
from src.probability.t1_gate import (
    ExecutableAction,
    ExecutableActionKind,
    T1GateStatus,
)
from src.reasoning.participant_classifier import ParticipantClassifier
from src.reasoning.pipeline import (
    MODEL_TIMEOUT_BUDGET_SECONDS,
    ReasoningPipeline,
    ReasoningPipelineRequest,
)
from src.reasoning.prompt_router import load_prompt_router
from src.reasoning.scenario_builder import REQUIRED_SCENARIOS, ScenarioInputs


@dataclass
class QueueModelClient:
    payloads: list[dict]
    requests: list[object] = field(default_factory=list)

    def complete(self, request):
        self.requests.append(request)
        payload = self.payloads.pop(0)
        return ModelResponse(
            output=request.response_schema.model_validate(payload),
            provider="fake", model="fake-model", usage={},
        )


ROOT = Path(__file__).parents[1]


def test_pipeline_uses_two_bounded_model_calls_for_all_three_scenarios() -> None:
    client = QueueModelClient([
        {
            "participant": "主力",
            "behavior_candidates": ["建仓", "观望"],
            "key_evidence": ["大单持续流入"],
            "contra_evidence": [],
        },
        {"behavior": "建仓", "key_evidence": ["冰点与吸筹信号自洽"]},
    ])
    router = load_prompt_router(ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine")
    pipeline = ReasoningPipeline(
        ParticipantClassifier(client, router),
        BehaviorForecaster(client, router, load_config()),
    )
    scenarios = {
        name: ScenarioInputs(
            behavior_forecasts={},
            opening_distribution={"高开": 0.2, "平开": 0.5, "低开": 0.3},
            first_passage={"target_first": 0.6, "stop_first": 0.2, "neither": 0.2},
            gate_status=T1GateStatus.PASSED,
            executable_actions=(
                ExecutableAction(ExecutableActionKind.PLAN_BUY_NEXT_TRADING_DAY, None),
            ),
            gate_reason="passed",
        )
        for name in REQUIRED_SCENARIOS
    }

    tree = pipeline.run(ReasoningPipelineRequest(
        cycle_position="冰点",
        policy_environment="无干预",
        materials={"capital_flow": "large-order inflow"},
        game_signals={"accumulation": True},
        sector_belief={"冰点": 0.7, "启动": 0.1, "发酵": 0.1, "高潮": 0.05, "退潮": 0.05},
        prior_weight=0.8,
        scenario_probabilities_and_gates=scenarios,
    ))

    assert len(tree.branches) == 3
    assert client.payloads == []
    assert MODEL_TIMEOUT_BUDGET_SECONDS == 90
    assert tree.branches[0].a_class["主力"].prior_weight == 0.8


def test_each_model_user_message_contains_one_canonical_market_analysis() -> None:
    marker = "MARKET_PARAGRAPH_MUST_APPEAR_ONCE"
    client = QueueModelClient([
        {
            "participant": "主力",
            "behavior_candidates": ["建仓"],
            "key_evidence": ["阶段一与大盘材料相互验证"],
            "contra_evidence": [],
        },
        {"behavior": "建仓", "key_evidence": ["结构与吸筹信号自洽"]},
    ])
    router = load_prompt_router(ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine")
    pipeline = ReasoningPipeline(
        ParticipantClassifier(client, router),
        BehaviorForecaster(client, router, load_config()),
    )
    market = {
        "status": "ready",
        "summary": marker,
        "data": {
            "indices": [{"name": "上证指数", "change_pct": -0.2}],
            "sections": [{"title": "一、盘面总览", "markdown": marker}],
        },
        "display_sections": [{"title": "一、盘面总览", "content": marker}],
    }
    materials = {
        "material_cache": {"state": "decision_snapshot"},
        "material_snapshot": {"materials": {"market": {"global": market}}},
        "market_analysis": market,
        "pa_stage1_analysis": {"summary": "STAGE1_TECHNICAL_CONTEXT"},
    }
    scenarios = {
        name: ScenarioInputs(
            behavior_forecasts={},
            opening_distribution={"高开": 0.2, "平开": 0.5, "低开": 0.3},
            first_passage={"target_first": 0.6, "stop_first": 0.2, "neither": 0.2},
            gate_status=T1GateStatus.PASSED,
            executable_actions=(),
            gate_reason="passed",
        )
        for name in REQUIRED_SCENARIOS
    }

    pipeline.run(ReasoningPipelineRequest(
        cycle_position="冰点",
        policy_environment="无干预",
        materials=materials,
        game_signals={"accumulation": True},
        sector_belief={"冰点": 0.7, "启动": 0.1, "发酵": 0.1, "高潮": 0.05, "退潮": 0.05},
        prior_weight=0.8,
        scenario_probabilities_and_gates=scenarios,
    ))

    assert len(client.requests) == 2
    for request in client.requests:
        assert request.user_prompt.count(marker) == 1
        assert "material_snapshot" not in request.user_prompt
        assert "material_cache" not in request.user_prompt
        assert "STAGE1_TECHNICAL_CONTEXT" in request.user_prompt
