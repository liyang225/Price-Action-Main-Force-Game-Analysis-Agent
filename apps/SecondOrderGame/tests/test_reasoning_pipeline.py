from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.hmm_filter import HMMFilter, load_config
from src.integration.model_adapter import ModelResponse
from src.reasoning.behavior_forecaster import BehaviorForecastRequest, BehaviorForecaster
from src.reasoning.participant_classifier import ParticipantClassifier
from src.reasoning.prompt_materials import project_reasoning_payload
from src.reasoning.scenario_advice_generator import ScenarioAdviceGenerator
from src.reasoning.cycle_classifier import CycleClassifier
from src.reasoning.prompt_router import load_prompt_router
from src.signals.dragon_tiger import DragonTigerSignal, SignalStatus


ROOT = Path(__file__).parents[1]


@dataclass
class QueueModelClient:
    payloads: list[dict]
    requests: list[object] = field(default_factory=list)

    def complete(self, request):
        self.requests.append(request)
        payload = self.payloads.pop(0)
        return ModelResponse(
            output=request.response_schema.model_validate(payload),
            provider="fake",
            model="fake-model",
            usage={},
        )


def test_production_prompt_registry_covers_common_and_participant_prompts() -> None:
    router = load_prompt_router(
        ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine"
    )

    assert router.common("参与者识别").name == "参与者识别.txt"
    assert router.config_version == 2
    assert router.route("冰点", "主力").name == "建仓.txt"
    assert router.route("高潮", "散户").name == "FOMO追高.txt"
    assert set(router.registered_paths) == {
        path.resolve() for path in (ROOT / "prompt_engine").rglob("*.txt")
    }


def test_classifier_returns_explicit_unavailable_without_evidence() -> None:
    classifier = ParticipantClassifier(
        QueueModelClient([]),
        load_prompt_router(ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine"),
    )

    result = classifier.classify({})

    assert result.status == "无法判断"
    assert result.participant is None
    assert result.behavior_candidates == ()


def test_cycle_classifier_uses_fixed_five_state_output_without_probabilities() -> None:
    client = QueueModelClient(
        [
            {
                "cycle_position": "发酵",
                "cycle_event": "平台整理",
                "confidence": "中",
                "consensus_state": "分歧",
                "consensus_direction": "未确认",
                "key_evidence": ["平台支撑未破，成交缩量20%"],
                "previous_state": "高潮",
                "transition_reason": "热度下降但结构未破",
            }
        ]
    )
    router = load_prompt_router(ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine")

    result = CycleClassifier(client, router).classify(
        {"sector": "半导体", "market": "结构分化"}, previous_state="高潮"
    )

    assert result.cycle_position == "发酵"
    assert result.cycle_event == "平台整理"
    assert result.to_dict()["role"] == "hmm_noise_sensor"


def test_cycle_model_receives_display_sections_once_with_stage1_context() -> None:
    marker = "MARKET_PARAGRAPH_MUST_APPEAR_ONCE"
    client = QueueModelClient(
        [
            {
                "cycle_position": "发酵",
                "cycle_event": "平台整理",
                "confidence": "中",
                "consensus_state": "分歧",
                "consensus_direction": "未确认",
                "key_evidence": ["阶段一与大盘结构一致"],
                "previous_state": None,
                "transition_reason": "结构待确认",
            }
        ]
    )
    router = load_prompt_router(ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine")
    market = {
        "summary": marker,
        "data": {"sections": [{"markdown": marker}]},
        "display_sections": [{"title": "盘面总览", "content": marker}],
    }

    CycleClassifier(client, router).classify(
        {
            "material_snapshot": {"materials": {"market": {"global": market}}},
            "market_analysis": market,
            "pa_stage1_analysis": {"trend": "STAGE1_TECHNICAL_CONTEXT"},
        }
    )

    prompt = client.requests[0].user_prompt
    assert prompt.count(marker) == 1
    assert "material_snapshot" not in prompt
    assert "STAGE1_TECHNICAL_CONTEXT" in prompt


def test_cycle_model_receives_sentiment_index_as_auxiliary_reference() -> None:
    client = QueueModelClient(
        [
            {
                "cycle_position": "启动",
                "confidence": "中",
                "consensus_state": "分歧",
                "consensus_direction": "转强",
                "key_evidence": ["低位结构改善"],
            }
        ]
    )
    router = load_prompt_router(ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine")

    CycleClassifier(client, router).classify(
        {
            "sector_analysis": {
                "sector_name": "半导体",
                "sentiment_index": 88,
                "sentiment_index_details": {"news_delta": 4},
            }
        }
    )

    payload = json.loads(client.requests[0].user_prompt)
    # 情绪指数保留作为辅助参考（不得用阈值映射到档位）
    assert payload["sector_analysis"]["sentiment_index"] == 88
    assert payload["sector_analysis"]["sentiment_index_details"] == {"news_delta": 4}
    # 周期判断仍不派生 sentiment_signal（那是非周期环节的连续信号）
    assert "sentiment_signal" not in payload


def test_continuous_sentiment_signal_is_explicitly_sent_to_non_cycle_models() -> None:
    materials = {
        "sector_analysis": {
            "sector_name": "半导体",
            "sentiment_index": 58,
            "sentiment_index_details": {
                "status": "calculated",
                "previous_index": 50,
                "daily_delta": 8,
                "news_delta": 5,
                "price_action_delta": 3,
            },
        }
    }
    router = load_prompt_router(
        ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine"
    )

    participant_client = QueueModelClient([{
        "participant": "散户",
        "behavior_candidates": ["理性跟随"],
        "key_evidence": ["情绪温和转强"],
    }])
    ParticipantClassifier(participant_client, router).classify(materials)

    behavior_client = QueueModelClient([
        {"behavior": "理性跟随", "key_evidence": ["情绪增量由新闻与价格共同驱动"]}
    ])
    BehaviorForecaster(behavior_client, router, load_config()).forecast(
        BehaviorForecastRequest(
            cycle_position="启动",
            participant="散户",
            policy_environment="无干预",
            materials=materials,
            game_signals={"consensus": "forming"},
            sector_belief={"冰点": 0.1, "启动": 0.6, "发酵": 0.2, "高潮": 0.05, "退潮": 0.05},
            prior_weight=0.8,
        )
    )

    advice_client = QueueModelClient([{
        "scenarios": {
            "超预期强": "观察转强能否延续。",
            "符合预期": "等待结构确认。",
            "低于预期": "警惕情绪回落。",
        }
    }])
    ScenarioAdviceGenerator(advice_client, router).generate(
        materials,
        cycle_position="启动",
        policy_environment="无干预",
        participant="散户",
        model_behavior="理性跟随",
        key_evidence=("情绪温和转强",),
        branches=(
            {"scenario": "超预期强"},
            {"scenario": "符合预期"},
            {"scenario": "低于预期"},
        ),
    )

    payloads = [
        json.loads(participant_client.requests[0].user_prompt),
        json.loads(behavior_client.requests[0].user_prompt),
        json.loads(advice_client.requests[0].user_prompt),
    ]
    for payload in payloads:
        signal = payload["sentiment_signal"]
        assert signal["usable"] is True
        assert signal["index"] == 58
        assert signal["news_delta"] == 5
        assert signal["price_action_delta"] == 3
        assert signal["cycle_classification_role"] == "excluded"

    unavailable = project_reasoning_payload(
        {
            "sector_analysis": {
                "sentiment_index": 50,
                "sentiment_index_details": {"status": "market_data_unavailable"},
            }
        }
    )
    assert unavailable["sentiment_signal"]["usable"] is False


def test_advice_tolerates_extra_status_key_in_scenarios() -> None:
    """LLM 结构漂移容错：三键齐全但误加 status 键时，剥离后正常返回。"""
    router = load_prompt_router(ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine")
    client = QueueModelClient([
        {
            "scenarios": {
                "超预期强": "观察转强能否延续。",
                "符合预期": "等待结构确认。",
                "低于预期": "警惕情绪回落。",
                "status": "insufficient_data",
            }
        }
    ])

    advice = ScenarioAdviceGenerator(client, router).generate(
        {},
        cycle_position="启动",
        policy_environment="无干预",
        participant="散户",
        model_behavior="理性跟随",
        key_evidence=("情绪温和转强",),
        branches=(
            {"scenario": "超预期强"},
            {"scenario": "符合预期"},
            {"scenario": "低于预期"},
        ),
    )

    assert set(advice) == {"超预期强", "符合预期", "低于预期"}
    assert "status" not in advice


def test_advice_missing_scenario_still_fails() -> None:
    """容错只针对多余键；缺少任一必需情景仍必须失败。"""
    import pytest

    router = load_prompt_router(ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine")
    client = QueueModelClient([
        {
            "scenarios": {
                "超预期强": "观察转强能否延续。",
                "符合预期": "等待结构确认。",
            }
        }
    ])

    with pytest.raises(ValueError, match="缺少情景"):
        ScenarioAdviceGenerator(client, router).generate(
            {},
            cycle_position="启动",
            policy_environment="无干预",
            participant="散户",
            model_behavior="理性跟随",
            key_evidence=("情绪温和转强",),
            branches=(
                {"scenario": "超预期强"},
                {"scenario": "符合预期"},
                {"scenario": "低于预期"},
            ),
        )


def test_cycle_model_receives_cached_subject_purpose_analysis() -> None:
    client = QueueModelClient(
        [
            {
                "cycle_position": "高潮",
                "confidence": "高",
                "consensus_state": "一致",
                "consensus_direction": "转弱",
                "key_evidence": ["主体目的显示高位利好出货"],
            }
        ]
    )
    router = load_prompt_router(
        ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine"
    )

    CycleClassifier(client, router).classify(
        {
            "news": {"items": [{"title": "订单公告"}]},
            "subject_purpose": {
                "true_purpose": "借利好出货",
                "signal_type": "混同均衡",
            },
        }
    )

    payload = json.loads(client.requests[0].user_prompt)
    assert payload["subject_purpose"]["true_purpose"] == "借利好出货"


def test_classifier_accepts_only_the_selected_participant_vocabulary() -> None:
    classifier = ParticipantClassifier(
        QueueModelClient(
            [{
                "participant": "散户",
                "behavior_candidates": ["理性跟随", "观望"],
                "key_evidence": ["趋势确认后出现小单持续参与"],
            }]
        ),
        load_prompt_router(ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine"),
    )

    result = classifier.classify({"volume": "rising"})

    assert result.participant == "散户"
    assert result.behavior_candidates == ("理性跟随", "观望")


def test_forecaster_rejects_impossible_model_label_and_computes_probabilities() -> None:
    client = QueueModelClient(
        [{"behavior": "拉升", "key_evidence": ["结构化材料支持拉升"]}]
    )
    router = load_prompt_router(
        ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine"
    )
    forecaster = BehaviorForecaster(client, router, load_config())

    result = forecaster.forecast(
        BehaviorForecastRequest(
            cycle_position="冰点",
            participant="主力",
            policy_environment="无干预",
            materials={"capital_flow": "large-order inflow"},
            game_signals={"accumulation": True},
            sector_belief={"冰点": 0.7, "启动": 0.1, "发酵": 0.1, "高潮": 0.05, "退潮": 0.05},
            prior_weight=0.8,
            possible_behaviors=frozenset({"建仓", "观望"}),
        )
    )

    assert result.model_behavior is None
    assert result.rejected_model_behavior == "拉升"
    assert set(result.probabilities) == {"建仓", "观望"}
    assert sum(result.probabilities.values()) == 1.0
    assert result.prior_weight == 0.8
    assert result.disclaimer == "专家先验推演，非统计估计"
    assert result.routing_config_version == 2


def test_retail_forecaster_enforces_the_retail_behavior_vocabulary() -> None:
    client = QueueModelClient(
        [{"behavior": "理性跟随", "key_evidence": ["放量但未出现情绪化追高"]}]
    )
    router = load_prompt_router(
        ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine"
    )
    forecaster = BehaviorForecaster(client, router, load_config())

    result = forecaster.forecast(
        BehaviorForecastRequest(
            cycle_position="启动",
            participant="散户",
            policy_environment="无干预",
            materials={"turnover": "rising"},
            game_signals={"consensus": "forming"},
            sector_belief={"冰点": 0.1, "启动": 0.6, "发酵": 0.2, "高潮": 0.05, "退潮": 0.05},
            prior_weight=0.8,
        )
    )

    assert result.model_behavior == "理性跟随"
    assert set(result.probabilities) == {
        "FOMO追高", "恐慌割肉", "观望", "理性跟随", "底部建仓", "高位减仓"
    }


def test_dragon_tiger_evidence_changes_distribution_and_is_auditable() -> None:
    client = QueueModelClient(
        [{"behavior": "建仓", "key_evidence": ["机构席位净买入"]}]
    )
    router = load_prompt_router(
        ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine"
    )
    forecaster = BehaviorForecaster(client, router, load_config())
    request = BehaviorForecastRequest(
        cycle_position="冰点",
        participant="主力",
        policy_environment="无干预",
        materials={"capital_flow": "large-order inflow"},
        game_signals={"accumulation": True},
        sector_belief={"冰点": 0.2, "启动": 0.2, "发酵": 0.2, "高潮": 0.2, "退潮": 0.2},
        prior_weight=1.0,
        dragon_tiger=DragonTigerSignal(
            date="2026-08-11",
            code="SZ.000001",
            status=SignalStatus.OK,
            institution_net_buy=700.0,
            hot_money_net_sell=200.0,
            source="AkShare",
            source_reference="stock_lhb_detail_em:2026-08-11",
        ),
    )

    result = forecaster.forecast(request)

    assert result.evidence_trace
    assert result.evidence_trace[0].source_reference.endswith("2026-08-11")
    assert result.evidence_trace[0].after != result.evidence_trace[0].before
    assert result.evidence_trace[0].observation_states["institution_net_buy"] == "启动"
    assert result.evidence_trace[0].config_version == load_config()["version"]


def test_missing_dragon_tiger_data_is_not_treated_as_neutral_evidence() -> None:
    client = QueueModelClient(
        [{"behavior": "建仓", "key_evidence": ["大单流入"]}]
    )
    router = load_prompt_router(
        ROOT / "config" / "prompt_routing.yaml", ROOT / "prompt_engine"
    )
    forecaster = BehaviorForecaster(client, router, load_config())
    request = BehaviorForecastRequest(
        cycle_position="冰点",
        participant="主力",
        policy_environment="无干预",
        materials={"capital_flow": "large-order inflow"},
        game_signals={"accumulation": True},
        sector_belief={"冰点": 0.2, "启动": 0.2, "发酵": 0.2, "高潮": 0.2, "退潮": 0.2},
        prior_weight=1.0,
        dragon_tiger=DragonTigerSignal(
            date="2026-08-11", code="SZ.000001", status=SignalStatus.NO_DATA
        ),
    )

    result = forecaster.forecast(request)

    assert result.evidence_trace == ()
