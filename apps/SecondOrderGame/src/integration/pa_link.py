"""PA stage-2 handoff primitives.

This module is deliberately UI-framework agnostic.  PA owns its technical
analysis widgets; SecondOrderGame owns this small, immutable handoff contract
and the lifecycle that starts a stage-2 analysis after PA finishes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
import math
from types import MappingProxyType
from typing import Any


class PALinkMode(str, Enum):
    """Whether PA runs independently (T+0) or invokes the second-order link."""

    T0 = "T+0"
    T1 = "T+1"


class PAStage2Status(str, Enum):
    """Lifecycle states exposed by a product page's second-level workspace."""

    EMPTY = "empty"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


class PADecisionPoint(str, Enum):
    """The two PA decision points and their canonical clock times."""

    MIDDAY = "midday"
    CLOSE = "close"

    @property
    def at(self) -> time:
        return time(11, 30) if self is PADecisionPoint.MIDDAY else time(15, 0)

    @classmethod
    def from_time(cls, value: time) -> "PADecisionPoint":
        if value.hour == 11 and value.minute == 30:
            return cls.MIDDAY
        if value.hour == 15 and value.minute == 0:
            return cls.CLOSE
        raise ValueError("decision time must be 11:30 or 15:00")


@dataclass(frozen=True, slots=True)
class PAStage2Input:
    """Structured PA output passed to SecondOrderGame without reinterpretation.

    Trading fields are optional because PA emits ``null`` for a no-trade
    decision.  The original payload is retained for audit and UI display.
    """

    symbol: str
    decision_point: PADecisionPoint
    should_trade: bool
    order_type: str | None = None
    entry_price: float | None = None
    take_profit_price: float | None = None
    stop_loss_price: float | None = None
    order_direction: str | None = None
    estimated_win_rate: int | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if not isinstance(self.decision_point, PADecisionPoint):
            raise TypeError("decision_point must be a PADecisionPoint")
        if not isinstance(self.should_trade, bool):
            raise TypeError("should_trade must be a bool")
        if self.order_type is not None and not isinstance(self.order_type, str):
            raise TypeError("order_type must be a string or None")
        for name in ("entry_price", "take_profit_price", "stop_loss_price"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise TypeError(f"{name} must be a number or None")
        if self.estimated_win_rate is not None and (
            isinstance(self.estimated_win_rate, bool)
            or not isinstance(self.estimated_win_rate, int)
            or not 0 <= self.estimated_win_rate <= 100
        ):
            raise ValueError("estimated_win_rate must be an integer from 0 to 100 or None")
        if self.order_direction is not None and not isinstance(self.order_direction, str):
            raise TypeError("order_direction must be a string or None")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    def to_dict(self) -> dict[str, Any]:
        """Return the safe PA contract used by UI and audit consumers."""
        return {
            "symbol": self.symbol,
            "decision_point": self.decision_point.value,
            "should_trade": self.should_trade,
            "order_type": self.order_type,
            "entry_price": self.entry_price,
            "take_profit_price": self.take_profit_price,
            "stop_loss_price": self.stop_loss_price,
            "order_direction": self.order_direction,
            "estimated_win_rate": self.estimated_win_rate,
            "payload": sanitize_pa_payload(self.payload),
        }

    @classmethod
    def from_pa_payload(cls, payload: Mapping[str, Any]) -> "PAStage2Input":
        """Adapt PA's JSON while preserving unknown fields in ``payload``."""
        if not isinstance(payload, Mapping):
            raise TypeError("PA stage-2 payload must be a mapping")
        point = payload.get("decision_point", payload.get("decisionPoint"))
        if isinstance(point, PADecisionPoint):
            decision_point = point
        elif isinstance(point, str):
            decision_point = PADecisionPoint.from_time(time.fromisoformat(point)) if ":" in point else PADecisionPoint(point)
        else:
            raise ValueError("PA stage-2 payload is missing decision_point")
        trade = payload.get("should_trade", payload.get("shouldTrade", False))
        return cls(
            symbol=str(payload.get("symbol", payload.get("code", ""))),
            decision_point=decision_point,
            should_trade=trade,
            order_type=payload.get("order_type", payload.get("orderType")),
            entry_price=payload.get("entry_price", payload.get("entryPrice")),
            take_profit_price=payload.get(
                "take_profit_price",
                payload.get("takeProfitPrice", payload.get("target_price")),
            ),
            stop_loss_price=payload.get("stop_loss_price", payload.get("stopLossPrice")),
            order_direction=payload.get("order_direction", payload.get("orderDirection")),
            estimated_win_rate=payload.get("estimated_win_rate", payload.get("estimatedWinRate")),
            payload=payload,
        )


@dataclass(frozen=True, slots=True)
class Stage2Completion:
    """Result delivered to the optional completion callback."""

    input: PAStage2Input
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class BridgeContext:
    """Point-in-time material supplied by the production data pipeline.

    PA owns the technical fields.  The context owns market/sector material and
    the already computed B/C inputs; keeping the two separate prevents the
    bridge from silently rewriting PA's win rate or risk/reward fields.
    """

    cycle_position: str = "冰点"
    policy_environment: str = "无干预"
    materials: Mapping[str, Any] = field(default_factory=dict)
    game_signals: Mapping[str, Any] = field(default_factory=dict)
    sector_belief: Mapping[str, float] = field(default_factory=dict)
    prior_weight: float = 1.0
    scenario_probabilities_and_gates: Mapping[str, Any] = field(default_factory=dict)
    scenario_gate_results: Mapping[str, Any] = field(default_factory=dict)
    source: str = "PA_Agent.stage2"
    stage2_completed_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("cycle_position", "policy_environment", "source"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")
        for name in (
            "materials",
            "game_signals",
            "sector_belief",
            "scenario_probabilities_and_gates",
            "scenario_gate_results",
        ):
            if not isinstance(getattr(self, name), Mapping):
                raise TypeError(f"{name} must be a mapping")
        if isinstance(self.prior_weight, bool) or not isinstance(self.prior_weight, (int, float)):
            raise TypeError("prior_weight must be a number")
        if not math.isfinite(self.prior_weight) or not 0 <= self.prior_weight <= 1:
            raise ValueError("prior_weight must be between 0 and 1")
        if self.stage2_completed_at is not None and not isinstance(self.stage2_completed_at, datetime):
            raise TypeError("stage2_completed_at must be a datetime or None")
        object.__setattr__(self, "materials", MappingProxyType(dict(self.materials)))
        object.__setattr__(self, "game_signals", MappingProxyType(dict(self.game_signals)))
        object.__setattr__(self, "sector_belief", MappingProxyType(dict(self.sector_belief)))
        object.__setattr__(self, "scenario_probabilities_and_gates", MappingProxyType(dict(self.scenario_probabilities_and_gates)))
        object.__setattr__(self, "scenario_gate_results", MappingProxyType(dict(self.scenario_gate_results)))


@dataclass(frozen=True, slots=True)
class SecondOrderInput:
    """Lossless, context-enriched input accepted by the reasoning pipeline."""

    pa: PAStage2Input
    cycle_position: str
    policy_environment: str
    materials: Mapping[str, Any]
    game_signals: Mapping[str, Any]
    sector_belief: Mapping[str, float]
    prior_weight: float
    scenario_probabilities_and_gates: Mapping[str, Any]
    scenario_gate_results: Mapping[str, Any]
    source_trace: Mapping[str, Any]

    @property
    def symbol(self) -> str:
        return self.pa.symbol

    @property
    def decision_point(self) -> PADecisionPoint:
        return self.pa.decision_point

    def to_pipeline_request(self) -> Any:
        """Create the public reasoning request without changing PA semantics."""
        from src.reasoning.pipeline import ReasoningPipelineRequest

        return ReasoningPipelineRequest(
            cycle_position=self.cycle_position,
            policy_environment=self.policy_environment,
            materials=self.materials,
            game_signals=self.game_signals,
            sector_belief=self.sector_belief,
            prior_weight=self.prior_weight,
            scenario_probabilities_and_gates=self.scenario_probabilities_and_gates,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "decision_point": self.decision_point.value,
            "pa": self.pa.to_dict(),
            "cycle_position": self.cycle_position,
            "policy_environment": self.policy_environment,
            "materials": dict(self.materials),
            "game_signals": dict(self.game_signals),
            "sector_belief": dict(self.sector_belief),
            "prior_weight": self.prior_weight,
            "scenario_gate_results": dict(self.scenario_gate_results),
            "source_trace": dict(self.source_trace),
        }


class PAStage2Bridge:
    """Adapt PA JSON and inject point-in-time context for SecondOrderGame."""

    def adapt(
        self,
        value: PAStage2Input | Mapping[str, Any],
        context: BridgeContext | None = None,
    ) -> SecondOrderInput:
        pa = value if isinstance(value, PAStage2Input) else PAStage2Input.from_pa_payload(value)
        context = context or BridgeContext()
        materials = dict(context.materials)
        # Keep PA's provider prompts and conversation history outside the
        # SecondOrder model boundary. Only this structured handoff is sent to
        # participant/cycle/behavior prompts.
        pa_stage2 = pa.to_dict()
        pa_stage2.pop("payload", None)
        materials["pa_stage2"] = pa_stage2
        trace = {
            "source": context.source,
            "symbol": pa.symbol,
            "decision_point": pa.decision_point.value,
            "pa_payload_fields": tuple(sorted(pa.payload)),
        }
        if context.stage2_completed_at is not None:
            trace["stage2_completed_at"] = context.stage2_completed_at.isoformat()
        return SecondOrderInput(
            pa=pa,
            cycle_position=context.cycle_position,
            policy_environment=context.policy_environment,
            materials=MappingProxyType(materials),
            game_signals=MappingProxyType(dict(context.game_signals)),
            sector_belief=MappingProxyType(dict(context.sector_belief)),
            prior_weight=context.prior_weight,
            scenario_probabilities_and_gates=context.scenario_probabilities_and_gates,
            scenario_gate_results=context.scenario_gate_results,
            source_trace=MappingProxyType(trace),
        )

    # ``bridge`` is intentionally an alias-friendly verb for PA host adapters.
    bridge = adapt


@dataclass(frozen=True, slots=True)
class PAWorkspaceState:
    """Render model for the product page's third-level tab bar."""

    symbol: str
    active_tab: str = "technical"
    stage2_status: PAStage2Status = PAStage2Status.EMPTY
    stage2_result: Any = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if self.active_tab not in {"technical", "second_order"}:
            raise ValueError("active_tab must be 'technical' or 'second_order'")
        if self.stage2_status is PAStage2Status.ERROR and not self.error:
            raise ValueError("error is required when stage2_status is error")

    def select(self, tab: str) -> "PAWorkspaceState":
        return PAWorkspaceState(self.symbol, tab, self.stage2_status, self.stage2_result, self.error)

    def loading(self) -> "PAWorkspaceState":
        return PAWorkspaceState(self.symbol, "second_order", PAStage2Status.LOADING)

    def ready(self, result: Any) -> "PAWorkspaceState":
        return PAWorkspaceState(self.symbol, "second_order", PAStage2Status.READY, result)

    def failed(self, error: Exception | str) -> "PAWorkspaceState":
        return PAWorkspaceState(self.symbol, "second_order", PAStage2Status.ERROR, error=str(error))


def build_product_tabs(technical_widget: Any, second_order_widget: Any) -> Any:
    """Build the nested PA product-page tabs without importing Qt at module load.

    PA keeps ownership of its terminal and technical-analysis widget.  This
    helper only supplies the third-level container, so the existing terminal
    remains tab one and the second-order workspace is tab two.
    """
    try:
        from PyQt6.QtWidgets import QTabWidget
    except ImportError as exc:  # pragma: no cover - depends on the host PA UI
        raise RuntimeError("PyQt6 is required to build PA product tabs") from exc
    tabs = QTabWidget()
    tabs.addTab(technical_widget, "技术分析")
    tabs.addTab(second_order_widget, "二阶博弈")
    return tabs


class PAStage2Link:
    """Schedule and invoke SecondOrderGame after each successful stage 2."""

    def __init__(
        self,
        mode: PALinkMode = PALinkMode.T0,
        *,
        on_stage2_complete: Callable[[Stage2Completion], Any] | None = None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        if not isinstance(mode, PALinkMode):
            raise TypeError("mode must be a PALinkMode")
        if on_stage2_complete is not None and not callable(on_stage2_complete):
            raise TypeError("on_stage2_complete must be callable")
        self.mode = mode
        self._callback = on_stage2_complete
        self._clock = clock

    def complete_stage2(self, value: PAStage2Input | Mapping[str, Any], *, success: bool = True) -> Any:
        """Notify the link after stage 2; failures and T+0 never invoke it."""
        if not isinstance(success, bool):
            raise TypeError("success must be a bool")
        stage2_input = value if isinstance(value, PAStage2Input) else PAStage2Input.from_pa_payload(value)
        if not success or self.mode is PALinkMode.T0 or self._callback is None:
            return None
        expected = self.decision_point()
        if stage2_input.decision_point is not expected:
            raise ValueError(
                f"stage-2 decision point {stage2_input.decision_point.value!r} "
                f"does not match scheduler point {expected.value!r}"
            )
        return self._callback(Stage2Completion(stage2_input, self._clock()))

    def decision_point(self, now: datetime | None = None) -> PADecisionPoint:
        """Resolve the current PA decision point using an injectable clock."""
        moment = now or self._clock()
        if moment.time() < PADecisionPoint.MIDDAY.at:
            raise ValueError("stage-2 completion is before the midday decision point")
        return PADecisionPoint.CLOSE if moment.time() >= PADecisionPoint.CLOSE.at else PADecisionPoint.MIDDAY


__all__ = [
    "BridgeContext",
    "PAStage2Input",
    "PAStage2Bridge",
    "PAStage2Link",
    "PAStage2Status",
    "PAWorkspaceState",
    "PADecisionPoint",
    "PALinkMode",
    "Stage2Completion",
    "build_product_tabs",
    "SecondOrderInput",
    "sanitize_pa_payload",
]


_PAYLOAD_SECRET_MARKERS = (
    "prompt",
    "message",
    "conversation",
    "rawresponse",
    "rawrequest",
    "completion",
    "apikey",
    "accesstoken",
    "authorization",
    "cookie",
    "sessionid",
)
_PAYLOAD_SECRET_KEYS = {
    "history",
    "conversationhistory",
    "analysisrecord",
    "debug",
    "raw",
}


def sanitize_pa_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Remove PA prompt/session internals before a payload reaches SO models."""
    if not isinstance(payload, Mapping):
        raise TypeError("PA payload must be a mapping")

    def clean(value: Any) -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for raw_key, child in value.items():
                name = str(raw_key)
                normalized = "".join(ch for ch in name.casefold() if ch.isalnum())
                if normalized in _PAYLOAD_SECRET_KEYS or any(
                    marker in normalized for marker in _PAYLOAD_SECRET_MARKERS
                ):
                    continue
                result[name] = clean(child)
            return result
        if isinstance(value, list | tuple):
            return [clean(item) for item in value]
        return value

    return clean(payload)
