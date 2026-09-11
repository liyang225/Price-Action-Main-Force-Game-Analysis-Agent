"""Program-owned A-probability forecasting around a constrained model label."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

import yaml
from pydantic import Field, model_validator

from src.hmm_filter import HMMFilter
from src.integration.model_adapter import ModelRequest, StrictModelOutput, StructuredModelClient
from src.labeler_constants import BEHAVIORS, MAIN_FORCE_BEHAVIORS, behaviors_for
from src.reasoning.prompt_materials import project_forecast_payload
from src.reasoning.prompt_router import PromptRouter
from src.probability.disclaimer import disclaimer_for_prior_weight
from src.signals.dragon_tiger import DragonTigerSignal, SignalStatus


DISCLAIMER = "专家先验推演，非统计估计"
_DEFAULT_EVIDENCE_CONFIG = Path(__file__).parents[2] / "config" / "dragon_tiger_inference.yaml"


class ScenarioExpectation(StrictModelOutput):
    """该情景下参与者会怎么做（提示词【情景推演】段要求的三情景行为倾向）。

    提示词一直要求这段内容，但此前没有声明在响应 schema 里，而系统提示词又禁止
    输出未声明字段，于是每次都被丢弃——应对树的三个情景因此携带同一份 A 类行为
    分布，无法区分，而 ARCHITECTURE §10.2 要求每个情景都给出各参与者最可能的行为。
    """

    trigger: str = ""
    behavior_shift: str = ""
    behavior_tendency: str = ""
    # 该情景下这个行为倾向自身的风险/后续，对应散户提示词里的「后续」一行。
    # 可选：主力提示词的三情景只写了「应对」，无对应内容时留空。
    risk: str = ""


_SCENARIO_FIELDS = ("trigger", "behavior_shift", "behavior_tendency", "risk")


class _BehaviorModelOutput(StrictModelOutput):
    """Shared behavior-forecast contract: one constrained label + scenario tendencies."""

    key_evidence: tuple[str, ...] = Field(min_length=1)
    scenario_expectations: dict[str, ScenarioExpectation] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _discard_unusable_scenarios(cls, value: object) -> object:
        """Never fail the call over the enrichment field.

        The label and its evidence decide the analysis; the scenario tendencies are
        extra explanation.  Drop entries that are not mappings, keep only the
        declared keys, and stringify the rest instead of rejecting the whole answer
        (which would cost a full corrective round-trip).
        """
        if not isinstance(value, Mapping):
            return value
        raw = value.get("scenario_expectations")
        if raw is None:
            return value
        cleaned: dict[str, dict[str, str]] = {}
        if isinstance(raw, Mapping):
            for key, item in raw.items():
                if not isinstance(item, Mapping):
                    continue
                cleaned[str(key)] = {
                    name: str(item.get(name) or "")
                    for name in _SCENARIO_FIELDS
                    if item.get(name) is not None
                }
        return {**value, "scenario_expectations": cleaned}


class MainForceBehaviorModelOutput(_BehaviorModelOutput):
    behavior: Literal["建仓", "震仓", "拉升", "出货", "观望", "狩猎止损"]


class RetailBehaviorModelOutput(_BehaviorModelOutput):
    behavior: Literal["FOMO追高", "恐慌割肉", "观望", "理性跟随", "底部建仓", "高位减仓"]


@dataclass(frozen=True, slots=True)
class BehaviorForecastRequest:
    cycle_position: str
    participant: str
    policy_environment: str
    materials: Mapping[str, Any]
    game_signals: Mapping[str, Any]
    sector_belief: Mapping[str, float]
    prior_weight: float
    possible_behaviors: frozenset[str] | None = None
    dragon_tiger: DragonTigerSignal | None = None


@dataclass(frozen=True, slots=True)
class EvidenceTrace:
    evidence_fields: tuple[str, ...]
    evidence_values: Mapping[str, float]
    source_reference: str
    config_version: int
    observation_states: Mapping[str, str]
    likelihoods: Mapping[str, Mapping[str, float]]
    before: Mapping[str, float]
    after: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class BehaviorForecast:
    model_behavior: str | None
    rejected_model_behavior: str | None
    key_evidence: tuple[str, ...]
    probabilities: Mapping[str, float]
    prior_weight: float
    disclaimer: str | None
    routing_config_version: int
    evidence_trace: tuple[EvidenceTrace, ...]
    scenario_expectations: Mapping[str, ScenarioExpectation] = field(
        default_factory=dict
    )


class BehaviorForecaster:
    def __init__(
        self,
        client: StructuredModelClient,
        router: PromptRouter,
        hmm_config: Mapping[str, Any],
        evidence_config_path: Path | str = _DEFAULT_EVIDENCE_CONFIG,
    ) -> None:
        self._client = client
        self._router = router
        self._hmm_config = dict(hmm_config)
        self._evidence_config = yaml.safe_load(
            Path(evidence_config_path).read_text(encoding="utf-8")
        )

    def forecast(self, request: BehaviorForecastRequest) -> BehaviorForecast:
        vocabulary = set(behaviors_for(request.participant))
        possible_behaviors = request.possible_behaviors or frozenset(vocabulary)
        if not possible_behaviors or not possible_behaviors <= vocabulary:
            raise ValueError("possible_behaviors must be a non-empty subset of the behavior vocabulary")
        if not 0 <= request.prior_weight <= 1:
            raise ValueError("prior_weight must be between 0 and 1")
        persona_path = self._router.common("人设与思维方式")
        routed_path = self._router.route(request.cycle_position, request.participant)
        persona = self._router.with_user_experience(persona_path.read_text(encoding="utf-8"))
        routed = routed_path.read_text(encoding="utf-8")
        response = self._client.complete(
            ModelRequest(
                system_prompt=f"{persona}\n\n{routed}",
                user_prompt=json.dumps(
                    project_forecast_payload(
                        {
                            "materials": request.materials,
                            "game_signals": request.game_signals,
                        }
                    ),
                    ensure_ascii=False, default=str, sort_keys=True,
                ),
                response_schema=(
                    MainForceBehaviorModelOutput
                    if request.participant == "主力"
                    else RetailBehaviorModelOutput
                ),
                timeout_seconds=30.0,
                prompt_sources=(str(persona_path), str(routed_path)),
            )
        )
        output = response.output
        filter_ = HMMFilter(self._hmm_config, sector_name="forecast")
        filter_.restore_belief(request.sector_belief)
        adjusted_belief, trace = self._apply_dragon_tiger(
            filter_.belief, request.participant, request.dragon_tiger
        )
        filter_.restore_belief(adjusted_belief)
        base = filter_.predict_behaviors(request.participant, request.policy_environment)
        allowed = {name: value for name, value in base.items() if name in possible_behaviors}
        allowed = _normalize(allowed)
        accepted = output.behavior if output.behavior in possible_behaviors else None
        return BehaviorForecast(
            model_behavior=accepted,
            rejected_model_behavior=None if accepted else output.behavior,
            key_evidence=output.key_evidence,
            probabilities=MappingProxyType(allowed),
            prior_weight=request.prior_weight,
            disclaimer=disclaimer_for_prior_weight(request.prior_weight),
            routing_config_version=self._router.config_version,
            evidence_trace=trace,
            scenario_expectations=_normalize_scenario_expectations(
                output.scenario_expectations
            ),
        )

    def _apply_dragon_tiger(
        self,
        belief: dict[str, float],
        participant: str,
        signal: DragonTigerSignal | None,
    ) -> tuple[dict[str, float], tuple[EvidenceTrace, ...]]:
        if participant != "主力" or signal is None or signal.status is not SignalStatus.OK:
            return belief, ()
        fields = tuple(
            name for name in (
                "institution_net_buy", "institution_net_sell",
                "hot_money_net_buy", "hot_money_net_sell",
            ) if getattr(signal, name) is not None
        )
        if not fields:
            return belief, ()
        before = dict(belief)
        adjusted = dict(belief)
        applied: dict[str, Mapping[str, float]] = {}
        for field in fields:
            observation = self._evidence_config["observation_states"][field]
            multipliers = {
                state: float(
                    self._hmm_config["confusion_matrix"][f"true_{state}"][
                        f"llm_{observation}"
                    ]
                )
                for state in adjusted
            }
            applied[field] = MappingProxyType(dict(multipliers))
            adjusted = {
                state: value * float(multipliers[state])
                for state, value in adjusted.items()
            }
        adjusted = _normalize(adjusted)
        trace = EvidenceTrace(
            evidence_fields=fields,
            evidence_values=MappingProxyType(
                {field: float(getattr(signal, field)) for field in fields}
            ),
            source_reference=signal.source_reference,
            config_version=int(self._hmm_config["version"]),
            observation_states=MappingProxyType(
                {field: self._evidence_config["observation_states"][field] for field in fields}
            ),
            likelihoods=MappingProxyType(applied),
            before=MappingProxyType(before),
            after=MappingProxyType(dict(adjusted)),
        )
        return adjusted, (trace,)


def _normalize(values: Mapping[str, float]) -> dict[str, float]:
    total = sum(values.values())
    if total <= 0:
        raise ValueError("behavior probabilities cannot be normalized")
    return {key: value / total for key, value in values.items()}


# Prompt files label the three scenarios 超预期 / 符合预期 / 低于预期; the response
# tree names them REQUIRED_SCENARIOS = (超预期强, 符合预期, 低于预期).  A test pins
# the two in sync so the tendencies cannot silently stop reaching the branches.
_SCENARIO_ALIASES: dict[str, str] = {
    "超预期": "超预期强",
    "符合预期": "符合预期",
    "低于预期": "低于预期",
}


def _canonical_scenario(label: str) -> str | None:
    """Map a model-written scenario label onto a branch name, or ``None``."""
    key = str(label).strip()
    if not key:
        return None
    if key in _SCENARIO_ALIASES:
        return _SCENARIO_ALIASES[key]
    for alias, name in _SCENARIO_ALIASES.items():
        if alias in key:
            return name
    return None


def _normalize_scenario_expectations(
    raw: Mapping[str, Any] | None,
) -> Mapping[str, ScenarioExpectation]:
    """Key the tendencies by the branch names the response tree uses."""
    if not isinstance(raw, Mapping):
        return MappingProxyType({})
    normalized: dict[str, ScenarioExpectation] = {}
    for key, item in raw.items():
        name = _canonical_scenario(key)
        if name is None or name in normalized or not isinstance(item, ScenarioExpectation):
            continue
        normalized[name] = item
    return MappingProxyType(normalized)


__all__ = [
    "BehaviorForecast",
    "BehaviorForecastRequest",
    "BehaviorForecaster",
    "EvidenceTrace",
    "MainForceBehaviorModelOutput",
    "RetailBehaviorModelOutput",
    "ScenarioExpectation",
]
