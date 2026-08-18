"""Deterministic three-branch scenario response tree assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from src.probability.t1_gate import (
    ExecutableAction,
    ExecutableActionKind,
    T1GateStatus,
)
from src.reasoning.behavior_forecaster import BehaviorForecast


REQUIRED_SCENARIOS = ("超预期强", "符合预期", "低于预期")


@dataclass(frozen=True, slots=True)
class ScenarioInputs:
    behavior_forecasts: Mapping[str, BehaviorForecast]
    opening_distribution: Mapping[str, float] | None
    first_passage: Mapping[str, float] | None
    gate_status: T1GateStatus
    executable_actions: tuple[ExecutableAction, ...]
    gate_reason: str


@dataclass(frozen=True, slots=True)
class ScenarioBranch:
    name: str
    status: str
    a_class: Mapping[str, BehaviorForecast]
    b_class: Mapping[str, float] | None
    c_class: Mapping[str, float] | None
    executable_actions: tuple[ExecutableAction, ...]
    action_advice: str
    gate_reason: str
    disclaimers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScenarioResponseTree:
    branches: tuple[ScenarioBranch, ...]
    is_single_path_price_forecast: bool = False
    analysis_metadata: Mapping[str, Any] = field(default_factory=dict)


class ScenarioResponseTreeBuilder:
    """Preserve probability shortages and keep advice subordinate to the gate."""

    def build(self, scenarios: Mapping[str, ScenarioInputs]) -> ScenarioResponseTree:
        if set(scenarios) != set(REQUIRED_SCENARIOS):
            raise ValueError("情景应对树必须且只能包含三种情景")
        branches = tuple(
            self._branch(name, scenarios[name]) for name in REQUIRED_SCENARIOS
        )
        return ScenarioResponseTree(branches)

    @staticmethod
    def _branch(name: str, inputs: ScenarioInputs) -> ScenarioBranch:
        if not inputs.behavior_forecasts:
            raise ValueError("每个情景必须包含参与者行为预测")
        # B 类（下一完整时段分布）不依赖止盈/止损价，永远计算并展示；C 类（首达概率）
        # 依赖止损价，仅在其缺失时置空。二者独立，不再相互拖累。
        b_class = (
            MappingProxyType(dict(inputs.opening_distribution))
            if inputs.opening_distribution is not None
            else None
        )
        c_class = (
            MappingProxyType(dict(inputs.first_passage))
            if inputs.first_passage is not None
            else None
        )
        if inputs.gate_status is T1GateStatus.INSUFFICIENT_DATA:
            status = T1GateStatus.INSUFFICIENT_DATA.value
            advice = "T+1 闸门数据不足，禁止新增买入；B 类下一时段概率仍可用于情境预判。"
            executable_actions = tuple(
                action for action in inputs.executable_actions
                if action.kind not in {
                    ExecutableActionKind.PLAN_BUY_THIS_AFTERNOON,
                    ExecutableActionKind.PLAN_BUY_NEXT_TRADING_DAY,
                }
            )
        else:
            status = inputs.gate_status.value
            advice = (
                "闸门通过：按可执行动作集合制定计划，并在实际情景出现后选择本分支。"
                if inputs.gate_status is T1GateStatus.PASSED
                else "闸门未通过：不新增买入，仅执行允许的风险降低动作。"
            )
            executable_actions = tuple(inputs.executable_actions)
        return ScenarioBranch(
            name=name,
            status=status,
            a_class=MappingProxyType(dict(inputs.behavior_forecasts)),
            b_class=b_class,
            c_class=c_class,
            executable_actions=executable_actions,
            action_advice=advice,
            gate_reason=inputs.gate_reason,
            disclaimers=MappingProxyType({
                f"{participant}:{behavior}": forecast.disclaimer
                for participant, forecast in inputs.behavior_forecasts.items()
                for behavior in forecast.probabilities
                if forecast.disclaimer is not None
            }),
        )


__all__ = [
    "REQUIRED_SCENARIOS", "ScenarioBranch", "ScenarioInputs",
    "ScenarioResponseTree", "ScenarioResponseTreeBuilder",
]
