"""LLM emotion-cycle observation used as an HMM noise sensor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from pydantic import Field

from src.integration.model_adapter import ModelRequest, StrictModelOutput, StructuredModelClient
from src.reasoning.prompt_materials import project_cycle_payload
from src.reasoning.prompt_router import PromptRouter


CYCLE_STATES = ("冰点", "启动", "发酵", "高潮", "退潮")


class CycleModelOutput(StrictModelOutput):
    cycle_position: Literal["冰点", "启动", "发酵", "高潮", "退潮"]
    cycle_event: Literal["无", "平台整理", "二次启动", "加速", "高位兑现", "破位转弱"] = "无"
    confidence: Literal["高", "中", "低"]
    consensus_state: Literal["一致", "分歧"]
    consensus_direction: Literal["转强", "转弱", "未确认"]
    key_evidence: tuple[str, ...] = Field(min_length=1)
    previous_state: Literal["冰点", "启动", "发酵", "高潮", "退潮"] | None = None
    transition_reason: str = ""


@dataclass(frozen=True, slots=True)
class CycleObservation:
    cycle_position: str
    cycle_event: str
    confidence: str
    consensus_state: str
    consensus_direction: str
    key_evidence: tuple[str, ...]
    previous_state: str | None
    transition_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "cycle_position": self.cycle_position,
            "cycle_event": self.cycle_event,
            "confidence": self.confidence,
            "consensus_state": self.consensus_state,
            "consensus_direction": self.consensus_direction,
            "key_evidence": list(self.key_evidence),
            "previous_state": self.previous_state,
            "transition_reason": self.transition_reason,
            "role": "hmm_noise_sensor",
        }


class CycleClassifier:
    def __init__(self, client: StructuredModelClient, router: PromptRouter) -> None:
        self._client = client
        self._router = router

    def classify(
        self, materials: Mapping[str, Any], *, previous_state: str | None = None
    ) -> CycleObservation:
        if not isinstance(materials, Mapping) or not materials:
            raise ValueError("情绪周期判断缺少分析材料")
        prompt_path = self._router.common("情绪周期判断")
        prompt = prompt_path.read_text(encoding="utf-8")
        request_materials = project_cycle_payload(materials)
        if previous_state in CYCLE_STATES:
            request_materials["previous_cycle_position"] = previous_state
        response = self._client.complete(
            ModelRequest(
                system_prompt=prompt,
                user_prompt=json.dumps(
                    request_materials, ensure_ascii=False, default=str, sort_keys=True
                ),
                response_schema=CycleModelOutput,
                timeout_seconds=30.0,
                prompt_sources=(str(prompt_path),),
            )
        )
        output = response.output
        return CycleObservation(
            cycle_position=output.cycle_position,
            cycle_event=output.cycle_event,
            confidence=output.confidence,
            consensus_state=output.consensus_state,
            consensus_direction=output.consensus_direction,
            key_evidence=output.key_evidence,
            # The previous state is program-owned input.  Keep the model's
            # echoed field constrained by schema, but never let it rewrite
            # the transition baseline used for audit/HMM persistence.
            previous_state=previous_state if previous_state in CYCLE_STATES else None,
            transition_reason=output.transition_reason,
        )


__all__ = ["CYCLE_STATES", "CycleClassifier", "CycleModelOutput", "CycleObservation"]
