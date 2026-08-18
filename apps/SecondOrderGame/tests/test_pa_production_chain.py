from __future__ import annotations

from datetime import datetime

from src.data.sentiment_ledger import SentimentLedger
from src.hmm_filter import HMMFilter, load_config
from src.integration import (
    BridgeContext,
    IndependentT1TradeGate,
    PAStage2Bridge,
    PAStage2Input,
    PALinkMode,
    ProductionOrchestrator,
    ProductionRunStatus,
    ProgressSink,
)
from src.probability import DecisionPoint
from src.probability.t1_gate import (
    ExecutableAction,
    ExecutableActionKind,
    T1GateResult,
    T1GateStatus,
)
from src.reasoning.behavior_forecaster import BehaviorForecast
from src.reasoning.cycle_classifier import CycleObservation
from src.reasoning.scenario_builder import (
    REQUIRED_SCENARIOS,
    ScenarioInputs,
    ScenarioResponseTreeBuilder,
)


def _pa(**changes) -> PAStage2Input:
    value = {
        "symbol": "000001.SZ",
        "decision_point": "midday",
        "should_trade": True,
        "entry_price": 10.0,
        "stop_loss_price": 9.5,
        "estimated_win_rate": 66,
        "technical_reason": "PA 原始理由",
        "sector_code": "SH.BK0001",
        "sector_name": "半导体",
    }
    value.update(changes)
    return PAStage2Input.from_pa_payload(value)


def _gate(status=T1GateStatus.PASSED) -> T1GateResult:
    actions = (
        ExecutableAction(ExecutableActionKind.PLAN_BUY_THIS_AFTERNOON, None),
    )
    return T1GateResult(
        status=status,
        decision_point=DecisionPoint.MIDDAY,
        executable_actions=actions,
        reason="secondary gate",
        favorable_opening_probability=0.6,
        neutral_opening_probability=0.2,
        adverse_opening_probability=0.2,
        target_first_probability=0.6,
        stop_first_probability=0.2,
        neither_probability=0.2,
        config_version=1,
    )


def test_bridge_keeps_pa_null_no_trade_fields_and_injects_auditable_context():
    pa = _pa(should_trade=False, entry_price=None, stop_loss_price=None, estimated_win_rate=None)
    result = PAStage2Bridge().adapt(
        pa,
        BridgeContext(
            cycle_position="启动",
            policy_environment="政策暖风",
            materials={"news": "cached"},
            game_signals={"contrarian_buy": False},
            sector_belief={"冰点": 0.2, "启动": 0.8},
            prior_weight=0.7,
        ),
    )

    assert result.pa.entry_price is None
    assert result.materials["pa_stage2"]["estimated_win_rate"] is None
    assert "payload" not in result.materials["pa_stage2"]
    assert result.materials["news"] == "cached"
    assert result.source_trace["source"] == "PA_Agent.stage2"
    assert result.to_dict()["pa"]["payload"]["technical_reason"] == "PA 原始理由"


def test_t1_requires_pa_and_secondary_gate_and_never_changes_pa_metrics():
    pa = _pa()
    result = IndependentT1TradeGate().evaluate(pa, _gate())
    assert result.gate_passed is True
    assert result.pa_gate_passed is True
    assert pa.entry_price == 10.0
    assert pa.estimated_win_rate == 66

    blocked = IndependentT1TradeGate().evaluate(_pa(should_trade=False), _gate())
    assert blocked.gate_passed is False
    assert blocked.status.value == "not_applicable"
    assert blocked.reason == "没有下单信号，T+1新增买入暂不评估"
    assert all("buy" not in action.kind.value for action in blocked.executable_actions)

    unavailable = IndependentT1TradeGate().evaluate(pa, _gate(T1GateStatus.INSUFFICIENT_DATA))
    assert unavailable.status.value == "insufficient_data"

    t0 = IndependentT1TradeGate().evaluate(pa, _gate(), mode=PALinkMode.T0)
    assert t0.is_applicable is False
    assert t0.status.value == "not_applicable"


class _FakePipeline:
    def run(self, request):
        forecast = BehaviorForecast(
            model_behavior="建仓",
            rejected_model_behavior=None,
            key_evidence=("evidence",),
            probabilities={"建仓": 1.0},
            prior_weight=request.prior_weight,
            disclaimer="专家先验推演，非统计估计" if request.prior_weight >= 0.2 else None,
            routing_config_version=1,
            evidence_trace=(),
        )
        scenarios = {
            name: ScenarioInputs(
                behavior_forecasts={"主力": forecast},
                opening_distribution={"gap_up": 0.6},
                first_passage={"target_first": 0.6},
                gate_status=T1GateStatus.PASSED,
                executable_actions=(),
                gate_reason="secondary gate",
            )
            for name in REQUIRED_SCENARIOS
        }
        return ScenarioResponseTreeBuilder().build(scenarios)


def test_production_orchestrator_runs_bridge_gate_and_page_state_with_retry():
    context = BridgeContext(
        materials={
            "probability_chain": {
                "opening_distribution": [
                    {
                        "outcome": "gap_up",
                        "probability": 0.6,
                        "prior_weight": 0.8,
                        "disclaimer": "专家先验推演，非统计估计",
                    }
                ]
            }
        },
        scenario_probabilities_and_gates={
            name: ScenarioInputs(
                behavior_forecasts={},
                opening_distribution={"gap_up": 0.6},
                first_passage={"target_first": 0.6},
                gate_status=T1GateStatus.PASSED,
                executable_actions=(),
                gate_reason="secondary gate",
            )
            for name in REQUIRED_SCENARIOS
        },
        scenario_gate_results={name: _gate() for name in REQUIRED_SCENARIOS},
        prior_weight=0.8,
    )
    orchestrator = ProductionOrchestrator(_FakePipeline(), clock=lambda: datetime(2026, 8, 12, 11, 30))
    loading = orchestrator.submit(_pa(), context=context)
    assert loading.status is ProductionRunStatus.LOADING
    ready = orchestrator.wait("000001.SZ", timeout=5)
    assert ready is not None
    assert ready.status is ProductionRunStatus.READY
    assert ready.result is not None
    assert all(gate.gate_passed for gate in ready.result.integrated_gates.values())
    assert ready.result.pa_metrics["estimated_win_rate"] == 66
    serialized = ready.result.to_dict()
    forecast = serialized["scenario_tree"]["branches"][0]["a_class"]["主力"]
    assert forecast["routing_config_version"] == 1
    assert serialized["scenario_tree"]["probability_chain"] == context.materials[
        "probability_chain"
    ]
    retrying = orchestrator.retry("000001.SZ")
    assert retrying.attempt == 2
    orchestrator.wait("000001.SZ", timeout=5)
    orchestrator.close()


def test_production_orchestrator_emits_stage_events_to_progress_sink():
    context = BridgeContext(
        materials={"probability_chain": {}},
        scenario_probabilities_and_gates={
            name: ScenarioInputs(
                behavior_forecasts={},
                opening_distribution={"gap_up": 0.6},
                first_passage={"target_first": 0.6},
                gate_status=T1GateStatus.PASSED,
                executable_actions=(),
                gate_reason="secondary gate",
            )
            for name in REQUIRED_SCENARIOS
        },
        scenario_gate_results={name: _gate() for name in REQUIRED_SCENARIOS},
    )
    sink = ProgressSink()
    orchestrator = ProductionOrchestrator(
        _FakePipeline(),
        progress_sink=sink,
        clock=lambda: datetime(2026, 8, 12, 11, 30),
    )

    orchestrator.run(_pa(), context=context)
    orchestrator.close()

    messages = [event.message for event in sink.events() if event.kind == "stage"]
    assert any("开始二阶推演" in message for message in messages)
    assert any("主导参与者" in message for message in messages)
    assert any("T+1 闸门" in message for message in messages)
    assert any("二阶推演完成" in message for message in messages)


def test_production_orchestrator_degrades_instead_of_raising_on_pipeline_failure():
    """容错兜底：推演阶段（参与者识别等模型环节）失败时降级返回，不中断、不弹错。

    对应线上报错 "model response failed ParticipantModelOutput validation"。
    """
    class _FailingPipeline:
        def run(self, request):
            raise RuntimeError("model response failed ParticipantModelOutput validation")

    context = BridgeContext(
        materials={"probability_chain": {}},
        scenario_probabilities_and_gates={
            name: ScenarioInputs(
                behavior_forecasts={},
                opening_distribution={"gap_up": 0.6},
                first_passage={"target_first": 0.6},
                gate_status=T1GateStatus.PASSED,
                executable_actions=(),
                gate_reason="secondary gate",
            )
            for name in REQUIRED_SCENARIOS
        },
        scenario_gate_results={name: _gate() for name in REQUIRED_SCENARIOS},
    )
    sink = ProgressSink()
    orchestrator = ProductionOrchestrator(
        _FailingPipeline(),
        progress_sink=sink,
        clock=lambda: datetime(2026, 8, 12, 11, 30),
    )

    result = orchestrator.run(_pa(), context=context)
    orchestrator.close()

    assert result.scenario_tree.branches == ()
    metadata = result.scenario_tree.analysis_metadata
    assert metadata["degraded"] is True
    assert metadata["participant_analysis"]["status"] == "degraded"
    assert "ParticipantModelOutput" in metadata["degraded_reason"]
    assert result.integrated_gates == {}
    messages = [event.message for event in sink.events() if event.kind == "stage"]
    assert any("推演降级" in message for message in messages)
    # 序列化后 UI 可正常渲染（空分支 + degraded 标记），下游 T+1 联动按无闸门处理
    serialized = result.to_dict()
    assert serialized["scenario_tree"]["branches"] == []
    assert serialized["scenario_tree"]["analysis_metadata"]["degraded"] is True
    assert serialized["integrated_gates"] == {}


def test_cycle_sensor_updates_persistent_sector_belief_once_per_closed_bar(tmp_path):
    hmm_config = load_config()
    context = BridgeContext(
        cycle_position="启动",
        policy_environment="无干预",
        materials={
            "sector_analysis": {"sector_name": "半导体"},
            "probability_chain": {},
        },
        game_signals={"bar_time": "2026-08-12 11:30:00"},
        sector_belief=HMMFilter(hmm_config, sector_name="半导体").belief,
        scenario_probabilities_and_gates={
            name: ScenarioInputs(
                behavior_forecasts={},
                opening_distribution={"gap_up": 0.6},
                first_passage={"target_first": 0.6},
                gate_status=T1GateStatus.PASSED,
                executable_actions=(),
                gate_reason="secondary gate",
            )
            for name in REQUIRED_SCENARIOS
        },
        scenario_gate_results={name: _gate() for name in REQUIRED_SCENARIOS},
    )

    class CycleSensor:
        def classify(self, materials, *, previous_state=None):
            return CycleObservation(
                cycle_position="发酵",
                cycle_event="平台整理",
                confidence="中",
                consensus_state="分歧",
                consensus_direction="转强",
                key_evidence=("量价结构改善 20%",),
                previous_state=previous_state,
                transition_reason="结构确认",
            )

    database = tmp_path / "sentiment.db"
    orchestrator = ProductionOrchestrator(
        _FakePipeline(),
        cycle_classifier=CycleSensor(),
        hmm_config=hmm_config,
        belief_database=database,
        clock=lambda: datetime(2026, 8, 12, 11, 30),
    )

    first = orchestrator.run(_pa(sector_name="半导体"), context=context)
    second = orchestrator.run(_pa(sector_name="半导体"), context=context)
    orchestrator.close()

    assert first.input.cycle_position == max(
        first.input.sector_belief, key=first.input.sector_belief.__getitem__
    )
    assert first.input.materials["cycle_observation"]["role"] == "hmm_noise_sensor"
    assert first.input.materials["cycle_observation"]["cycle_position"] == "发酵"
    assert first.input.materials["sector_analysis"]["cycle_position_source"] == "hmm_posterior"
    assert first.input.sector_belief == second.input.sector_belief
    assert set(first.input.materials["participant_priors"]["散户"]) == {
        "FOMO追高",
        "恐慌割肉",
        "观望",
        "理性跟随",
        "底部建仓",
        "高位减仓",
    }
    assert set(first.input.materials["participant_posteriors"]["散户"]) == {
        "FOMO追高",
        "恐慌割肉",
        "观望",
        "理性跟随",
        "底部建仓",
        "高位减仓",
    }
    assert first.input.materials["participant_priors"] != first.input.materials["participant_posteriors"]
    with SentimentLedger(database) as ledger:
        checkpoint = ledger.load_belief("SH.BK0001")
    assert checkpoint is not None
    assert checkpoint.last_k120m_closed_at == datetime(2026, 8, 12, 11, 30)


def test_missing_closed_bar_does_not_advance_hmm_with_wall_clock(tmp_path):
    hmm_config = load_config()
    context = BridgeContext(
        cycle_position="启动",
        materials={
            "sector_analysis": {"sector_name": "半导体"},
            "probability_chain": {},
        },
        game_signals={"status": "insufficient_data"},
        sector_belief=HMMFilter(hmm_config, sector_name="半导体").belief,
        scenario_probabilities_and_gates={
            name: ScenarioInputs(
                behavior_forecasts={},
                opening_distribution={"gap_up": 0.6},
                first_passage={"target_first": 0.6},
                gate_status=T1GateStatus.PASSED,
                executable_actions=(),
                gate_reason="secondary gate",
            )
            for name in REQUIRED_SCENARIOS
        },
        scenario_gate_results={name: _gate() for name in REQUIRED_SCENARIOS},
    )

    class CycleSensor:
        def classify(self, materials, *, previous_state=None):
            return CycleObservation(
                cycle_position="发酵",
                cycle_event="无",
                confidence="低",
                consensus_state="分歧",
                consensus_direction="未确认",
                key_evidence=("缺少完整 K_120M",),
                previous_state=previous_state,
                transition_reason="",
            )

    database = tmp_path / "sentiment.db"
    orchestrator = ProductionOrchestrator(
        _FakePipeline(),
        cycle_classifier=CycleSensor(),
        hmm_config=hmm_config,
        belief_database=database,
        clock=lambda: datetime(2026, 8, 12, 11, 30),
    )

    result = orchestrator.run(_pa(sector_name="半导体"), context=context)
    orchestrator.close()

    with SentimentLedger(database) as ledger:
        checkpoint = ledger.load_belief("半导体")
    assert checkpoint is None
    assert result.input.materials["cycle_observation"]["hmm_update"] == "skipped_no_closed_bar"
