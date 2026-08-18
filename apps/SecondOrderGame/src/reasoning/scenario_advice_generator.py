"""LLM-generated response advice for the three opening scenarios.

This extends the model's role into narrative advice text (ADR-0023).  The
T+1 gate and executable actions remain program-owned; the advice text is the
model's free-form reaction to each scenario.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from pydantic import Field, model_validator

from src.integration.model_adapter import (
    ModelRequest,
    StrictModelOutput,
    StructuredModelClient,
)
from src.reasoning.prompt_materials import project_reasoning_payload
from src.reasoning.prompt_router import PromptRouter
from src.reasoning.scenario_builder import REQUIRED_SCENARIOS


class ScenarioAdviceModelOutput(StrictModelOutput):
    scenarios: dict[str, str]

    @model_validator(mode="after")
    def validate_scenarios(self) -> "ScenarioAdviceModelOutput":
        scenarios = dict(self.scenarios)
        missing = set(REQUIRED_SCENARIOS) - set(scenarios)
        if missing:
            raise ValueError(f"应对方案缺少情景: {sorted(missing)}")
        if any(not scenarios[key].strip() for key in REQUIRED_SCENARIOS):
            raise ValueError("每个情景的应对方案不能为空")
        extras = set(scenarios) - set(REQUIRED_SCENARIOS)
        if extras:
            # LLM 结构漂移容错：三个必需键齐全时，剥离多余键（如模型误加的
            # status/confidence 等），避免单个多余键使整条分析链路失败。
            scenarios = {key: scenarios[key] for key in REQUIRED_SCENARIOS}
            return self.model_copy(update={"scenarios": scenarios})
        return self


class ScenarioAdviceGenerator:
    """Ask the model for one free-form advice paragraph per scenario."""

    def __init__(self, client: StructuredModelClient, router: PromptRouter) -> None:
        self._client = client
        self._router = router

    def generate(
        self,
        materials: Mapping[str, Any],
        *,
        cycle_position: str,
        policy_environment: str,
        participant: str,
        model_behavior: str | None,
        key_evidence: Sequence[str],
        branches: Sequence[Mapping[str, Any]],
    ) -> dict[str, str]:
        prompt_path = self._router.common("情景应对")
        system_prompt = self._router.with_user_experience(
            prompt_path.read_text(encoding="utf-8")
        )
        response = self._client.complete(
            ModelRequest(
                system_prompt=system_prompt,
                user_prompt=json.dumps(
                    project_reasoning_payload(
                        {
                            "cycle_position": cycle_position,
                            "policy_environment": policy_environment,
                            "participant": participant,
                            "model_behavior": model_behavior,
                            "key_evidence": list(key_evidence),
                            "scenarios": [dict(branch) for branch in branches],
                            "materials": materials,
                        }
                    ),
                    ensure_ascii=False,
                    default=str,
                    sort_keys=True,
                ),
                response_schema=ScenarioAdviceModelOutput,
                timeout_seconds=30.0,
                prompt_sources=(str(prompt_path),),
            )
        )
        return dict(response.output.scenarios)


__all__ = ["ScenarioAdviceGenerator", "ScenarioAdviceModelOutput"]
