"""Constrained participant and current-behavior classification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from src.integration.model_adapter import ModelRequest, StrictModelOutput, StructuredModelClient
from src.labeler_constants import behaviors_for
from src.reasoning.prompt_materials import project_reasoning_payload
from src.reasoning.prompt_router import PromptRouter


Behavior = Literal[
    "建仓", "震仓", "拉升", "出货", "观望", "狩猎止损",
    "FOMO追高", "恐慌割肉", "理性跟随", "底部建仓", "高位减仓",
]


class ParticipantModelOutput(StrictModelOutput):
    participant: Literal["主力", "散户"]
    # This qualitative label is part of the participant prompt contract.
    confidence: Literal["高", "中", "低"] = "中"
    behavior_candidates: tuple[Behavior, ...] = Field(min_length=1)
    key_evidence: tuple[str, ...] = Field(min_length=1)
    contra_evidence: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_participant_vocabulary(self) -> "ParticipantModelOutput":
        illegal = set(self.behavior_candidates) - set(behaviors_for(self.participant))
        if illegal:
            raise ValueError(f"行为候选不属于{self.participant}词表：{sorted(illegal)}")
        return self


@dataclass(frozen=True, slots=True)
class ParticipantClassification:
    status: Literal["ok", "无法判断"]
    participant: str | None
    behavior_candidates: tuple[str, ...]
    key_evidence: tuple[str, ...]
    contra_evidence: tuple[str, ...] = ()
    confidence: str = "中"


class ParticipantClassifier:
    def __init__(self, client: StructuredModelClient, router: PromptRouter) -> None:
        self._client = client
        self._router = router

    def classify(self, materials: Mapping[str, Any]) -> ParticipantClassification:
        if not isinstance(materials, Mapping):
            raise TypeError("materials must be a mapping")
        if not materials:
            return ParticipantClassification("无法判断", None, (), ())
        prompt_path = self._router.common("参与者识别")
        prompt = prompt_path.read_text(encoding="utf-8")
        response = self._client.complete(
            ModelRequest(
                system_prompt=prompt,
                user_prompt=json.dumps(
                    project_reasoning_payload(materials),
                    ensure_ascii=False,
                    default=str,
                    sort_keys=True,
                ),
                response_schema=ParticipantModelOutput,
                timeout_seconds=30.0,
                prompt_sources=(str(prompt_path),),
            )
        )
        output = response.output
        return ParticipantClassification(
            "ok", output.participant, tuple(dict.fromkeys(output.behavior_candidates)),
            output.key_evidence, output.contra_evidence, output.confidence,
        )


__all__ = ["ParticipantClassification", "ParticipantClassifier", "ParticipantModelOutput"]
