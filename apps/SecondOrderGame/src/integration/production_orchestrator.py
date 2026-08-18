"""Production PA → bridge → reasoning → gate → page orchestration."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping

from src.hmm_filter import HMMFilter
from src.integration.pa_link import (
    BridgeContext,
    PAStage2Bridge,
    PAStage2Input,
    PAWorkspaceState,
    PALinkMode,
    SecondOrderInput,
    Stage2Completion,
)
from src.integration.progress import ProgressEvent, ProgressSink
from src.integration.t1_trade_gate import (
    IndependentT1TradeGate,
    IntegratedT1GateResult,
    IntegratedT1GateStatus,
)
from src.probability.t1_gate import T1GateResult
from src.reasoning.scenario_builder import (
    ScenarioResponseTree,
)


class ProductionRunStatus(str, Enum):
    EMPTY = "empty"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ProductionAnalysisResult:
    """Complete render/audit payload for one PA product page analysis."""

    input: SecondOrderInput
    scenario_tree: ScenarioResponseTree
    integrated_gates: Mapping[str, IntegratedT1GateResult]
    completed_at: datetime

    @property
    def pa_metrics(self) -> dict[str, Any]:
        pa = self.input.pa
        return {
            "should_trade": pa.should_trade,
            "entry_price": pa.entry_price,
            "stop_loss_price": pa.stop_loss_price,
            "estimated_win_rate": pa.estimated_win_rate,
            "payload": pa.to_dict()["payload"],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "input": self.input.to_dict(),
            "pa_metrics": self.pa_metrics,
            "scenario_tree": {
                "probability_chain": dict(
                    self.input.materials.get("probability_chain") or {}
                ),
                "analysis_metadata": dict(self.scenario_tree.analysis_metadata),
                "branches": [
                    {
                        "name": branch.name,
                        "status": branch.status,
                        "a_class": {
                            participant: {
                                "probabilities": dict(forecast.probabilities),
                                "prior_weight": forecast.prior_weight,
                                "disclaimer": forecast.disclaimer,
                                "model_behavior": forecast.model_behavior,
                                "routing_config_version": forecast.routing_config_version,
                                "key_evidence": list(forecast.key_evidence),
                                "rejected_model_behavior": forecast.rejected_model_behavior,
                            }
                            for participant, forecast in branch.a_class.items()
                        },
                        "b_class": dict(branch.b_class) if branch.b_class is not None else None,
                        "c_class": dict(branch.c_class) if branch.c_class is not None else None,
                        "gate_reason": branch.gate_reason,
                        "action_advice": branch.action_advice,
                        "executable_actions": [
                            action.to_dict() for action in branch.executable_actions
                        ],
                        "disclaimers": dict(branch.disclaimers),
                    }
                    for branch in self.scenario_tree.branches
                ]
            },
            "integrated_gates": {
                name: gate.to_dict() for name, gate in self.integrated_gates.items()
            },
            "completed_at": self.completed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ProductionRunState:
    symbol: str
    status: ProductionRunStatus
    workspace: PAWorkspaceState
    result: ProductionAnalysisResult | None = None
    error: str | None = None
    attempt: int = 0


class ProductionOrchestrator:
    """Own the production lifecycle while keeping each domain module injectable.

    ``submit`` marks the page loading before dispatching work, so a PA stage-2
    completion always starts the second-order run immediately.  ``run`` remains
    available as a synchronous seam for deterministic CLI/batch execution.
    """

    def __init__(
        self,
        pipeline: Any,
        *,
        mode: PALinkMode = PALinkMode.T1,
        bridge: PAStage2Bridge | None = None,
        trade_gate: IndependentT1TradeGate | None = None,
        context_provider: Callable[[PAStage2Input], BridgeContext] | None = None,
        clock: Callable[[], datetime] = datetime.now,
        executor: ThreadPoolExecutor | None = None,
        cycle_classifier: Any | None = None,
        hmm_config: Mapping[str, Any] | None = None,
        belief_database: Path | str | None = None,
        progress_sink: ProgressSink | None = None,
        llm_observation_sink: Callable[[str, str, str], None] | None = None,
    ) -> None:
        if not isinstance(mode, PALinkMode):
            raise TypeError("mode must be a PALinkMode")
        if not hasattr(pipeline, "run") or not callable(pipeline.run):
            raise TypeError("pipeline must expose a callable run(request) method")
        self.mode = mode
        self._pipeline = pipeline
        self._bridge = bridge or PAStage2Bridge()
        self._trade_gate = trade_gate or IndependentT1TradeGate()
        self._context_provider = context_provider or (lambda _input: BridgeContext())
        self._clock = clock
        self._cycle_classifier = cycle_classifier
        self._progress_sink = progress_sink
        self._hmm_config = dict(hmm_config) if hmm_config is not None else None
        self._llm_observation_sink = llm_observation_sink
        self._belief_database = (
            Path(belief_database)
            if belief_database is not None
            else Path(__file__).resolve().parents[2] / "runtime" / "sentiment.db"
        )
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="second-order")
        self._owns_executor = executor is None
        self._lock = RLock()
        self._latest: dict[str, ProductionRunState] = {}
        self._latest_event: dict[str, tuple[PAStage2Input, BridgeContext, int]] = {}
        self._futures: dict[str, Future[ProductionAnalysisResult]] = {}

    def on_stage2_complete(self, event: Stage2Completion) -> ProductionRunState:
        """Callback compatible with :class:`PAStage2Link`."""
        if not isinstance(event, Stage2Completion):
            raise TypeError("event must be a Stage2Completion")
        context = self._context_provider(event.input)
        if not isinstance(context, BridgeContext):
            raise TypeError("context provider must return BridgeContext")
        context = replace(context, stage2_completed_at=event.completed_at)
        return self.submit(event.input, context=context)

    def submit(
        self,
        value: PAStage2Input | Mapping[str, Any],
        *,
        context: BridgeContext | None = None,
    ) -> ProductionRunState:
        pa = value if isinstance(value, PAStage2Input) else PAStage2Input.from_pa_payload(value)
        ctx = context or self._context_provider(pa)
        if not isinstance(ctx, BridgeContext):
            raise TypeError("context provider must return BridgeContext")
        with self._lock:
            previous = self._latest.get(pa.symbol)
            attempt = (previous.attempt if previous else 0) + 1
            state = ProductionRunState(
                symbol=pa.symbol,
                status=ProductionRunStatus.LOADING,
                workspace=PAWorkspaceState(pa.symbol).loading(),
                attempt=attempt,
            )
            self._latest[pa.symbol] = state
            self._latest_event[pa.symbol] = (pa, ctx, attempt)
            future = self._executor.submit(self.run, pa, context=ctx)
            self._futures[pa.symbol] = future
            future.add_done_callback(lambda done, symbol=pa.symbol, n=attempt: self._complete(symbol, n, done))
            return state

    def run(
        self,
        value: PAStage2Input | Mapping[str, Any],
        *,
        context: BridgeContext | None = None,
    ) -> ProductionAnalysisResult:
        pa = value if isinstance(value, PAStage2Input) else PAStage2Input.from_pa_payload(value)
        ctx = context or self._context_provider(pa)
        if not isinstance(ctx, BridgeContext):
            raise TypeError("context provider must return BridgeContext")
        self._emit("stage", "开始二阶推演", stage="model", source="orchestrator", symbol=pa.symbol)
        ctx = self._observe_cycle(pa, ctx)
        self._emit(
            "stage",
            f"情绪周期判定：{ctx.cycle_position}",
            stage="model",
            source="cycle_classifier",
            symbol=pa.symbol,
        )
        enriched = self._bridge.adapt(pa, ctx)
        try:
            tree = self._pipeline.run(enriched.to_pipeline_request())
        except Exception as exc:  # noqa: BLE001 — 容错兜底：推演（参与者识别/行为推演/应对树）失败不中断
            self._emit(
                "stage",
                f"推演降级：{str(exc) or type(exc).__name__}",
                stage="model",
                source="orchestrator",
                symbol=pa.symbol,
            )
            return self._degraded_result(pa, enriched, exc)
        participant, behavior = self._participant_summary(tree)
        self._emit(
            "stage",
            f"主导参与者：{participant}；主导行为：{behavior}",
            stage="model",
            source="participant_classifier",
            symbol=pa.symbol,
        )
        self._emit("stage", "三情景应对树已生成", stage="scenarios", source="scenario_builder", symbol=pa.symbol)
        integrated = self._integrate_gates(enriched, tree)
        self._emit("stage", "T+1 闸门整合完成", stage="gate", source="t1_trade_gate", symbol=pa.symbol)
        branches = tree.branches
        if self.mode is PALinkMode.T1:
            branches = tuple(
                replace(
                    branch,
                    status=integrated[branch.name].status.value,
                    executable_actions=integrated[branch.name].executable_actions,
                    gate_reason=integrated[branch.name].reason,
                )
                for branch in tree.branches
            )
        self._emit("stage", "二阶推演完成", stage="finish", source="orchestrator", symbol=pa.symbol)
        return ProductionAnalysisResult(
            input=enriched,
            scenario_tree=replace(tree, branches=branches),
            integrated_gates=integrated,
            completed_at=self._clock(),
        )

    def _emit(
        self,
        kind: str,
        message: str,
        *,
        stage: str = "",
        source: str = "",
        symbol: str = "",
    ) -> None:
        if self._progress_sink is None:
            return
        self._progress_sink.emit(
            ProgressEvent(
                ts=self._clock(),
                symbol=symbol,
                kind=kind,
                stage=stage,
                message=message,
                source=source,
            )
        )

    def _degraded_result(
        self,
        pa: PAStage2Input,
        enriched: SecondOrderInput,
        reason: Exception,
    ) -> ProductionAnalysisResult:
        """Build a non-blocking degraded result when the reasoning pipeline fails.

        The scenario tree carries no branches and a ``degraded`` participant
        marker; the empty gate map makes downstream T+1 linkage treat this as
        "no valid gate" (no new buy — the safe direction).  The original error
        text is preserved in ``analysis_metadata`` for audit.
        """
        message = str(reason) or type(reason).__name__
        degraded_tree = ScenarioResponseTree(
            branches=(),
            analysis_metadata={
                "participant_analysis": {
                    "status": "degraded",
                    "participant": None,
                    "behavior_candidates": [],
                    "key_evidence": [],
                    "contra_evidence": [],
                    "reason": message,
                },
                "degraded": True,
                "degraded_reason": message,
            },
        )
        return ProductionAnalysisResult(
            input=enriched,
            scenario_tree=degraded_tree,
            integrated_gates={},
            completed_at=self._clock(),
        )

    @staticmethod
    def _participant_summary(tree: ScenarioResponseTree) -> tuple[str, str]:
        """Extract the dominant participant and behavior from already-computed fields."""
        metadata = getattr(tree, "analysis_metadata", None) or {}
        participant_analysis = metadata.get("participant_analysis") or {}
        participant = participant_analysis.get("participant") or "无法判断"
        branches = getattr(tree, "branches", ()) or ()
        first = branches[0] if branches else None
        a_class = getattr(first, "a_class", None) if first is not None else None
        forecast = a_class.get(participant) if isinstance(a_class, Mapping) else None
        if forecast is None and isinstance(a_class, Mapping) and a_class:
            forecast = next(iter(a_class.values()))
        model_behavior = getattr(forecast, "model_behavior", None)
        if not model_behavior:
            probabilities = getattr(forecast, "probabilities", None)
            if isinstance(probabilities, Mapping) and probabilities:
                model_behavior = max(probabilities, key=probabilities.get)
        return participant, model_behavior or "无法判断"


    def _observe_cycle(self, pa: PAStage2Input, context: BridgeContext) -> BridgeContext:
        """Classify the five-state cycle, then treat that label as HMM evidence."""
        if self._cycle_classifier is None:
            return context
        prior_belief = dict(context.sector_belief)
        states = {"冰点", "启动", "发酵", "高潮", "退潮"}
        previous = context.cycle_position if context.cycle_position in states else None
        try:
            observation = self._cycle_classifier.classify(
                context.materials, previous_state=previous
            )
        except Exception as exc:
            if previous is None:
                raise RuntimeError(f"情绪周期判断不可用：{exc}") from exc
            return replace(
                context,
                materials={
                    **dict(context.materials),
                    "cycle_observation": {
                        "status": "fallback_pa",
                        "cycle_position": previous,
                        "key_evidence": [],
                        "reason": str(exc),
                    },
                },
            )

        belief = dict(context.sector_belief)
        hmm_update_status = "not_configured"
        if self._hmm_config is not None:
            updated = self._update_belief(pa, context, observation.cycle_position)
            if updated is not None:
                belief = updated
                hmm_update_status = "updated_or_reused"
            else:
                hmm_update_status = "skipped_no_closed_bar"
        effective_cycle = (
            max(belief, key=belief.__getitem__)
            if self._hmm_config is not None and belief
            else observation.cycle_position
        )
        if self._llm_observation_sink is not None:
            try:
                self._llm_observation_sink(
                    _sector_code_from_materials(context.materials),
                    _trading_date_from_materials(context.materials),
                    observation.cycle_position,
                )
            except Exception:  # noqa: BLE001 — C-count recording must not break the decision
                pass
        materials = dict(context.materials)
        materials["cycle_observation"] = {
            **observation.to_dict(),
            "hmm_update": hmm_update_status,
            "effective_cycle_position": effective_cycle,
        }
        sector_analysis = materials.get("sector_analysis")
        if isinstance(sector_analysis, Mapping):
            materials["sector_analysis"] = {
                **dict(sector_analysis),
                "cycle_position": effective_cycle,
                "llm_observation": observation.cycle_position,
                "cycle_position_source": "hmm_posterior",
                "consensus_state": observation.consensus_state,
                "consensus_direction": observation.consensus_direction,
            }
        if self._hmm_config is not None:
            # Contexts constructed outside ProductionContextBuilder may not
            # carry the pre-observation distribution.  Derive it from their
            # supplied belief before consuming the current observation.
            if "participant_priors" not in materials:
                prior_filter = HMMFilter(self._hmm_config, sector_name="production")
                prior_filter.restore_belief(prior_belief)
                materials["participant_priors"] = {
                    participant: prior_filter.predict_behaviors(
                        participant, context.policy_environment
                    )
                    for participant in ("主力", "散户")
                }
            filter_ = HMMFilter(self._hmm_config, sector_name="production")
            filter_.restore_belief(belief)
            # Keep the pre-observation distribution supplied by the context
            # intact.  The UI presents it alongside this distribution so the
            # HMM update caused by the current K_120M observation is auditable.
            materials["participant_posteriors"] = {
                participant: filter_.predict_behaviors(
                    participant, context.policy_environment
                )
                for participant in ("主力", "散户")
            }
        return replace(
            context,
            cycle_position=effective_cycle,
            sector_belief=belief,
            materials=materials,
        )

    def _update_belief(
        self, pa: PAStage2Input, context: BridgeContext, observed_cycle: str
    ) -> dict[str, float] | None:
        from src.data.sentiment_ledger import SentimentLedger
        from src.reasoning.belief_updater import BeliefUpdater, K120MCloseEvent

        sector_material = context.materials.get("sector_analysis")
        sector_material = sector_material if isinstance(sector_material, Mapping) else {}
        raw_sector_code = sector_material.get("sector_code") or pa.payload.get("sector_code")
        if not isinstance(raw_sector_code, str) or not raw_sector_code.strip():
            raise ValueError("HMM 更新缺少规范化 sector_code")
        sector_code = raw_sector_code.strip()
        signal_bar = context.game_signals.get("bar_time")
        try:
            closed_at = datetime.fromisoformat(str(signal_bar))
        except (TypeError, ValueError):
            return None
        if closed_at.tzinfo is not None:
            closed_at = closed_at.replace(tzinfo=None)
        self._belief_database.parent.mkdir(parents=True, exist_ok=True)
        with SentimentLedger(self._belief_database) as ledger:
            updater = BeliefUpdater(self._hmm_config or {}, ledger)
            update = updater.update(
                K120MCloseEvent(
                    sector_code=sector_code,
                    closed_at=closed_at,
                    observed_cycle_state=observed_cycle,
                    is_complete=True,
                )
            )
            belief = update.belief if update is not None else updater.belief_for(sector_code)
        if belief is None:
            raise RuntimeError("HMM 情绪信念未能生成")
        return dict(belief)

    def state(self, symbol: str) -> ProductionRunState | None:
        with self._lock:
            return self._latest.get(symbol)

    def wait(self, symbol: str, timeout: float | None = None) -> ProductionRunState | None:
        future = self._futures.get(symbol)
        if future is None:
            return self.state(symbol)
        try:
            future.result(timeout=timeout)
        except TimeoutError:
            pass
        return self.state(symbol)

    def retry(self, symbol: str) -> ProductionRunState:
        latest = self._latest_event.get(symbol)
        if latest is None:
            raise KeyError(f"no stage-2 run exists for {symbol!r}")
        pa, context, _ = latest
        return self.submit(pa, context=context)

    def close(self) -> None:
        if self._owns_executor:
            self._executor.shutdown(wait=True)

    def _complete(self, symbol: str, attempt: int, future: Future[ProductionAnalysisResult]) -> None:
        try:
            result = future.result()
        except Exception as exc:  # surface a durable page error; never hide it
            state = ProductionRunState(
                symbol=symbol,
                status=ProductionRunStatus.ERROR,
                workspace=PAWorkspaceState(symbol).failed(exc),
                error=str(exc),
                attempt=attempt,
            )
        else:
            state = ProductionRunState(
                symbol=symbol,
                status=ProductionRunStatus.READY,
                workspace=PAWorkspaceState(symbol).ready(result),
                result=result,
                attempt=attempt,
            )
        with self._lock:
            current = self._latest.get(symbol)
            if current is None or current.attempt == attempt:
                self._latest[symbol] = state

    def _integrate_gates(
        self,
        enriched: SecondOrderInput,
        tree: ScenarioResponseTree,
    ) -> dict[str, IntegratedT1GateResult]:
        supplied = enriched.scenario_gate_results
        result: dict[str, IntegratedT1GateResult] = {}
        for branch in tree.branches:
            secondary = supplied.get(branch.name)
            if not isinstance(secondary, T1GateResult):
                result[branch.name] = IntegratedT1GateResult(
                    mode=self.mode,
                    status=(
                        IntegratedT1GateStatus.NOT_APPLICABLE
                        if self.mode is PALinkMode.T0
                        else IntegratedT1GateStatus.INSUFFICIENT_DATA
                    ),
                    pa_gate_passed=enriched.pa.should_trade,
                    second_order_gate_passed=None,
                    executable_actions=tuple(),
                    reason="缺少该情景的独立二阶闸门结果，禁止新增买入",
                    second_order_status=None,
                )
            else:
                result[branch.name] = self._trade_gate.evaluate(
                    enriched.pa, secondary, mode=self.mode
                )
        return result

    @classmethod
    def from_pa_model_client(
        cls,
        model_client: Any,
        *,
        root: Any = None,
        mode: PALinkMode = PALinkMode.T1,
        **kwargs: Any,
    ) -> "ProductionOrchestrator":
        """Assemble the production prompt/model/reasoning chain in one place."""
        from src.hmm_filter import load_config
        from src.labeler.calibration import load_production_hmm_config
        from src.reasoning.behavior_forecaster import BehaviorForecaster
        from src.reasoning.cycle_classifier import CycleClassifier
        from src.reasoning.participant_classifier import ParticipantClassifier
        from src.reasoning.pipeline import ReasoningPipeline
        from src.reasoning.prompt_router import load_prompt_router
        from src.reasoning.scenario_advice_generator import ScenarioAdviceGenerator

        project_root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
        router = load_prompt_router(
            project_root / "config" / "prompt_routing.yaml",
            project_root / "prompt_engine",
        )
        try:
            hmm_config = load_production_hmm_config()
        except Exception:  # noqa: BLE001 — calibration must never break startup
            hmm_config = load_config()
        pipeline = ReasoningPipeline(
            ParticipantClassifier(model_client, router),
            BehaviorForecaster(model_client, router, hmm_config),
            advice_generator=ScenarioAdviceGenerator(model_client, router),
        )
        return cls(
            pipeline,
            mode=mode,
            cycle_classifier=CycleClassifier(model_client, router),
            hmm_config=hmm_config,
            **kwargs,
        )


def build_production_orchestrator(
    model_client: Any,
    *,
    root: Any = None,
    mode: PALinkMode = PALinkMode.T1,
    **kwargs: Any,
) -> ProductionOrchestrator:
    """Functional factory used by PA hosts during application startup."""
    return ProductionOrchestrator.from_pa_model_client(
        model_client, root=root, mode=mode, **kwargs
    )


def _sector_code_from_materials(materials: Mapping[str, Any]) -> str:
    sector = materials.get("sector_analysis")
    if isinstance(sector, Mapping):
        code = sector.get("sector_code")
        if isinstance(code, str) and code.strip():
            return code.strip()
    return "unknown"


def _trading_date_from_materials(materials: Mapping[str, Any]) -> str:
    window = materials.get("market_window")
    if isinstance(window, Mapping):
        end = window.get("end")
        if isinstance(end, str) and end.strip():
            return end.strip()[:10]
    sector = materials.get("sector_analysis")
    if isinstance(sector, Mapping):
        session = sector.get("trading_session")
        if isinstance(session, Mapping):
            trading_date = session.get("trading_date")
            if isinstance(trading_date, str) and trading_date.strip():
                return trading_date.strip()[:10]
    return "unknown"


__all__ = [
    "ProductionAnalysisResult",
    "ProductionOrchestrator",
    "ProductionRunState",
    "ProductionRunStatus",
    "build_production_orchestrator",
]
