"""Composition of PA's gate and the independent SecondOrderGame T+1 gate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from src.integration.pa_link import PAStage2Input, PALinkMode
from src.probability.t1_gate import (
    ExecutableAction,
    ExecutableActionKind,
    T1GateResult,
    T1GateStatus,
)


class IntegratedT1GateStatus(str, Enum):
    """The final program-owned status exposed to a PA order workflow."""

    PASSED = "passed"
    BLOCKED = "blocked"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class IntegratedT1GateResult:
    """Independent gate result; PA and SecondOrderGame decisions stay visible."""

    mode: PALinkMode
    status: IntegratedT1GateStatus
    pa_gate_passed: bool
    second_order_gate_passed: bool | None
    executable_actions: tuple[ExecutableAction, ...]
    reason: str
    second_order_status: T1GateStatus | None

    @property
    def gate_passed(self) -> bool:
        return self.status is IntegratedT1GateStatus.PASSED

    @property
    def is_applicable(self) -> bool:
        return self.mode is PALinkMode.T1

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "status": self.status.value,
            "gate_passed": self.gate_passed,
            "is_applicable": self.is_applicable,
            "pa_gate_passed": self.pa_gate_passed,
            "second_order_gate_passed": self.second_order_gate_passed,
            "second_order_status": self.second_order_status.value if self.second_order_status else None,
            "executable_actions": [action.to_dict() for action in self.executable_actions],
            "reason": self.reason,
        }


class IndependentT1TradeGate:
    """Require both gates in T+1; never edits PA prices or win rate."""

    def evaluate(
        self,
        pa_input: PAStage2Input | Mapping[str, Any],
        second_order_gate: T1GateResult,
        *,
        mode: PALinkMode = PALinkMode.T1,
    ) -> IntegratedT1GateResult:
        if not isinstance(mode, PALinkMode):
            raise TypeError("mode must be a PALinkMode")
        pa = pa_input if isinstance(pa_input, PAStage2Input) else PAStage2Input.from_pa_payload(pa_input)
        if not isinstance(second_order_gate, T1GateResult):
            raise TypeError("second_order_gate must be a T1GateResult")
        pa_passed, pa_reason = _pa_gate(pa)
        if mode is PALinkMode.T0:
            return IntegratedT1GateResult(
                mode=mode,
                status=IntegratedT1GateStatus.NOT_APPLICABLE,
                pa_gate_passed=pa_passed,
                second_order_gate_passed=None,
                executable_actions=tuple(),
                reason="T+0 mode does not attach the independent SecondOrderGame gate",
                second_order_status=None,
            )

        secondary_passed = second_order_gate.gate_passed
        # Without a PA order signal there is no candidate new buy for T+1 to
        # evaluate. Preserve any sell/risk-reduction actions, but do not report
        # the absent order as a blocked gate.
        if not pa_passed:
            actions = tuple(
                action for action in second_order_gate.executable_actions
                if not _is_buy(action.kind)
            )
            return IntegratedT1GateResult(
                mode=mode,
                status=IntegratedT1GateStatus.NOT_APPLICABLE,
                pa_gate_passed=False,
                second_order_gate_passed=secondary_passed,
                executable_actions=actions,
                reason="没有下单信号，T+1新增买入暂不评估",
                second_order_status=second_order_gate.status,
            )
        if second_order_gate.status is T1GateStatus.INSUFFICIENT_DATA:
            status = IntegratedT1GateStatus.INSUFFICIENT_DATA
            reason = f"二阶数据不足，T+1 新增买入被阻断；{pa_reason}"
        elif not pa_passed:
            status = IntegratedT1GateStatus.BLOCKED
            reason = f"PA 原闸门未通过：{pa_reason}"
        elif not secondary_passed:
            status = IntegratedT1GateStatus.BLOCKED
            reason = f"二阶独立闸门未通过：{second_order_gate.reason}"
        else:
            status = IntegratedT1GateStatus.PASSED
            reason = "PA 原闸门与二阶独立闸门均通过"

        actions = second_order_gate.executable_actions
        if status is not IntegratedT1GateStatus.PASSED:
            actions = tuple(action for action in actions if not _is_buy(action.kind))
        return IntegratedT1GateResult(
            mode=mode,
            status=status,
            pa_gate_passed=pa_passed,
            second_order_gate_passed=secondary_passed,
            executable_actions=actions,
            reason=reason,
            second_order_status=second_order_gate.status,
        )

    check = evaluate


def _pa_gate(pa: PAStage2Input) -> tuple[bool, str]:
    payload = pa.payload
    for key in ("pa_gate_passed", "trade_gate_passed", "risk_reward_gate_passed"):
        if key in payload:
            value = payload[key]
            if not isinstance(value, bool):
                raise TypeError(f"{key} must be a bool when present")
            reason = str(payload.get("pa_gate_reason", "PA 阶段 2 原闸门结果"))
            return value, reason
    return pa.should_trade, "PA 阶段 2 should_trade 原闸门结果"


def _is_buy(kind: ExecutableActionKind) -> bool:
    return kind in {
        ExecutableActionKind.PLAN_BUY_THIS_AFTERNOON,
        ExecutableActionKind.PLAN_BUY_NEXT_TRADING_DAY,
    }


# Compatibility aliases for host integrations that use the shorter names.
T1TradeGate = IndependentT1TradeGate
T1TradeGateResult = IntegratedT1GateResult


__all__ = [
    "IndependentT1TradeGate",
    "IntegratedT1GateResult",
    "IntegratedT1GateStatus",
    "T1TradeGate",
    "T1TradeGateResult",
]
