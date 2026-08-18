from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import src.integration.production_context as production_context_module
from src.data.fake_client import FakeMarketDataSource
from src.data.models import Bar
from src.data.models import NewsItem
from src.integration.pa_link import PAStage2Input
from src.data.daily_cache import DailyMaterialCache
from src.data.sentiment_ledger import SentimentLedger
from src.integration.production_context import ProductionContextBuilder
from src.probability.t1_gate import T1GateStatus
from src.reasoning.scenario_builder import REQUIRED_SCENARIOS


@pytest.fixture(autouse=True)
def _isolate_sentiment_database(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_init = ProductionContextBuilder.__init__

    def isolated_init(self, market_source, *args, **kwargs):
        kwargs.setdefault("sentiment_database", tmp_path / "sentiment.db")
        original_init(self, market_source, *args, **kwargs)

    monkeypatch.setattr(ProductionContextBuilder, "__init__", isolated_init)


def _bars(days: int = 45) -> list[Bar]:
    result: list[Bar] = []
    start = datetime(2026, 5, 1, 11, 30)
    for index in range(days):
        day = (start + timedelta(days=index)).date().isoformat()
        base = 100.0 + index * 0.08
        result.extend(
            [
                Bar(f"{day} 11:30:00", base, base + 1.2, base - 0.7, base + 0.3, 1000 + index, 100_000),
                Bar(f"{day} 15:00:00", base + 0.3, base + 1.5, base - 0.5, base + 0.6, 1100 + index, 110_000),
            ]
        )
    return result


def _pa(**changes: object) -> PAStage2Input:
    payload: dict[str, object] = {
        "symbol": "000001.SZ",
        "decision_point": "close",
        "should_trade": True,
        "entry_price": 103.0,
        "stop_loss_price": 98.0,
        "take_profit_price": 108.0,
        "cycle_position": "启动",
        "sector_code": "SH.BK0001",
        "sector_name": "半导体",
    }
    payload.update(changes)
    return PAStage2Input.from_pa_payload(payload)


def test_builder_runs_market_signal_hmm_probability_and_gate_chain() -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={
            ("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars
        }
    )

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
    ).build(_pa())

    assert context.cycle_position == "启动"
    assert set(context.sector_belief) == {"冰点", "启动", "发酵", "高潮", "退潮"}
    assert sum(context.sector_belief.values()) == pytest.approx(1.0)
    assert context.game_signals["role"] == "observation_feature"
    assert set(context.scenario_probabilities_and_gates) == set(REQUIRED_SCENARIOS)
    assert set(context.scenario_gate_results) == set(REQUIRED_SCENARIOS)
    assert all(
        item.gate_status in {T1GateStatus.PASSED, T1GateStatus.BLOCKED, T1GateStatus.INSUFFICIENT_DATA}
        for item in context.scenario_probabilities_and_gates.values()
    )
    assert context.materials["probability_chain"]["opening_distribution"]
    assert context.materials["probability_chain"]["first_passage"]
    assert context.materials["news"]["status"] == "empty"
    assert context.prior_weight == pytest.approx(1.0)
    assert set(context.materials["participant_priors"]["散户"]) == {
        "FOMO追高", "恐慌割肉", "观望", "理性跟随", "底部建仓", "高位减仓"
    }
    assert sum(context.materials["participant_priors"]["散户"].values()) == pytest.approx(1.0)
    assert context.materials["material_cache"]["status"] == "not_configured"
    assert context.materials["sector_analysis"]["cycle_position_source"] == "pa_payload"


def test_builder_injects_pa_stage1_diagnosis_for_kline_reasoning() -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars}
    )

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
    ).build(
        _pa(
            stage1_diagnosis={
                "trend": "上升趋势",
                "kline_structure": "缩量回踩支撑",
            }
        )
    )

    assert context.materials["pa_stage1_analysis"] == {
        "trend": "上升趋势",
        "kline_structure": "缩量回踩支撑",
    }


def test_builder_freezes_all_material_categories_when_cache_is_configured(tmp_path) -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars}
    )
    cache = DailyMaterialCache(tmp_path / "archives", clock=lambda: datetime(2026, 8, 12, 11, 30))

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
        material_cache=cache,
    ).build(_pa(sector_name="半导体"))

    status = context.materials["material_cache"]
    snapshot = context.materials["material_snapshot"]["materials"]
    assert status["state"] == "decision_snapshot"
    assert set(snapshot) == {
        "market", "sector", "news", "scored_news", "sector_registry", "stock_signals"
    }
    assert "半导体" in snapshot["sector"]
    assert "000001.SZ" in snapshot["stock_signals"]


def test_production_decision_reads_prefetched_news_without_searching_network(tmp_path) -> None:
    bars = _bars()

    class Source(FakeMarketDataSource):
        def search_news(self, keyword: str):
            raise AssertionError(f"decision path must not search news: {keyword}")

    source = Source(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars}
    )
    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 12, 11, 30)
    )
    cache.put(
        "news",
        "半导体",
        (
            NewsItem(
                "预取资讯",
                "",
                "https://example.test/prefetched",
                "2026-08-12 10:00:00",
            ),
        ),
    )

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
        material_cache=cache,
    ).build(_pa(sector_name="半导体"))

    assert context.materials["news"]["source"] == "daily_material_cache"
    assert context.materials["news"]["items"][0]["title"] == "预取资讯"


def test_production_context_projects_cached_subject_purpose_to_downstream_materials(
    tmp_path,
) -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars}
    )
    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 12, 11, 30)
    )
    cache.put(
        "news",
        "半导体",
        (NewsItem("高位发布订单公告", "金额未披露", "", "2026-08-12"),),
    )
    cache.put(
        "subject_purpose",
        "半导体",
        {
            "status": "ready",
            "sector_name": "半导体",
            "news_count": 1,
            "message_type": "利好公告",
            "publisher": "上市公司",
            "true_purpose": "借利好出货",
            "confidence": "高",
            "signal_type": "混同均衡",
            "key_evidence": ["公告缺少订单金额"],
            "follow_up_expectation": "冲高回落",
        },
    )

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
        material_cache=cache,
    ).build(_pa(sector_name="半导体"))

    assert context.materials["subject_purpose"]["true_purpose"] == "借利好出货"
    snapshot = context.materials["material_snapshot"]["materials"]
    assert snapshot["subject_purpose"]["半导体"]["signal_type"] == "混同均衡"


def test_production_decision_computes_and_exposes_sentiment_from_cached_news(tmp_path) -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars}
    )
    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 12, 11, 30)
    )
    cache.put(
        "news",
        "半导体",
        (NewsItem("芯片需求增长，政策支持", "行业景气回升", "", "2026-08-12"),),
    )

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
        material_cache=cache,
    ).build(_pa(sector_name="半导体"))

    assert context.materials["sector_analysis"]["sentiment_index"] is not None
    assert context.materials["sector_analysis"]["sentiment_index"] > 50


def test_sentiment_ledger_uses_required_canonical_sector_code(tmp_path) -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars}
    )

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
        sentiment_database=tmp_path / "ledger.db",
    ).build(_pa(sector_code="HK.HSI Constituent", sector_name="芯片别名"))

    assert context.materials["sector_analysis"]["sector_code"] == "HK.HSI Constituent"
    with SentimentLedger(tmp_path / "ledger.db") as ledger:
        assert ledger.load("HK.HSI Constituent") is not None
        assert ledger.load("芯片别名") is None


def test_sector_code_validation_is_deferred_to_market_source(tmp_path) -> None:
    class Source(FakeMarketDataSource):
        def validate_sector_code(self, sector_code: str) -> None:
            raise RuntimeError(f"富途 OpenD 无法获取板块 {sector_code}: unknown plate code")

    source = Source(
        kline_data={
            ("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): _bars()
        }
    )

    with pytest.raises(RuntimeError, match="富途 OpenD 无法获取板块 US.ANYTHING"):
        ProductionContextBuilder(
            source,
            today=lambda: datetime(2026, 8, 12).date(),
            history_days=538,
            sentiment_database=tmp_path / "ledger.db",
        ).build(_pa(sector_code="US.ANYTHING"))


def test_missing_sector_code_is_rejected_instead_of_falling_back_to_symbol(tmp_path) -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars}
    )

    with pytest.raises(ValueError, match="sector_code"):
        ProductionContextBuilder(
            source,
            today=lambda: datetime(2026, 8, 12).date(),
            history_days=538,
        ).build(_pa(sector_code=None))


def test_price_component_reads_sector_daily_bars_not_payload_daily_return(tmp_path) -> None:
    bars = _bars()
    sector_daily = [
        Bar("2026-08-10", 100, 101, 99, 100, 1000, 100_000),
        Bar("2026-08-11", 100, 101, 94, 95, 1000, 100_000),
        Bar("2026-08-12", 95, 96, 88, 90, 1000, 100_000),
    ]
    source = FakeMarketDataSource(
        kline_data={
            ("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars,
            ("SH.BK0001", "K_DAY", "2025-02-20", "2026-08-12"): sector_daily,
        }
    )

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
    ).build(_pa(daily_return=0.50))

    details = context.materials["sector_analysis"]["sentiment_index_details"]
    assert details["daily_return"] == pytest.approx(90 / 95 - 1)
    assert details["two_day_return"] == pytest.approx(90 / 100 - 1)
    assert details["price_action_delta"] < 0


def test_non_trading_day_does_not_create_or_decay_ledger_state(tmp_path) -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars},
        trading_days=("2026-08-11",),
    )
    database = tmp_path / "ledger.db"

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
        sentiment_database=database,
    ).build(_pa())

    details = context.materials["sector_analysis"]["sentiment_index_details"]
    assert details["status"] == "non_trading_day"
    with SentimentLedger(database) as ledger:
        assert ledger.load("SH.BK0001") is None


def test_unavailable_sector_market_data_is_not_reported_as_non_trading_day(tmp_path) -> None:
    bars = _bars()
    sector_key = ("SH.BK0001", "K_DAY", "2025-02-20", "2026-08-12")
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars},
        failures={
            ("get_kline", sector_key): "sector quote unavailable",
            "get_trading_days": "calendar unavailable",
        },
    )
    database = tmp_path / "ledger.db"

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
        sentiment_database=database,
    ).build(_pa())

    sector = context.materials["sector_analysis"]
    assert sector["trading_session"]["source"] == "unavailable"
    assert sector["sentiment_index_details"]["status"] == "market_data_unavailable"
    with SentimentLedger(database) as ledger:
        assert ledger.load("SH.BK0001") is None


def test_fundamental_baseline_is_used_only_when_sector_is_first_registered(tmp_path) -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars}
    )

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
    ).build(_pa(sector_fundamental_baseline=60))

    details = context.materials["sector_analysis"]["sentiment_index_details"]
    assert details["previous_index"] == 60
    assert details["sentiment_index"] == 59


def test_intraday_news_refresh_recalculates_from_one_fixed_daily_base(tmp_path) -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars}
    )
    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 12, 11, 30)
    )
    builder = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
        material_cache=cache,
    )

    first = builder.build(_pa(sector_name="半导体"))
    cache.put(
        "news",
        "半导体",
        (NewsItem("芯片需求增长，政策支持", "行业景气回升", "", "2026-08-12"),),
    )
    second = builder.build(_pa(sector_name="半导体"))
    third = builder.build(_pa(sector_name="半导体"))

    assert first.materials["sector_analysis"]["sentiment_index"] == 50
    assert second.materials["sector_analysis"]["sentiment_index"] > 50
    assert third.materials["sector_analysis"]["sentiment_index"] == second.materials["sector_analysis"]["sentiment_index"]


def test_decision_snapshot_projects_only_current_symbol_and_sector(tmp_path) -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars}
    )
    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 12, 11, 30)
    )
    cache.put("news", "旧板块", {"status": "ready", "items": ["不得泄漏"]})
    cache.put("stock_signals", "999999.SZ", {"marker": "不得泄漏"})

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
        material_cache=cache,
    ).build(_pa(sector_name="半导体"))

    snapshot = context.materials["material_snapshot"]["materials"]
    assert set(snapshot["sector"]) == {"半导体"}
    assert set(snapshot["news"]) == {"半导体"}
    assert set(snapshot["stock_signals"]) == {"000001.SZ"}


def test_missing_target_is_explicitly_insufficient_and_blocks_new_buy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={
            ("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars
        }
    )

    market_loads = 0

    def load_market_once(payload: object, *, as_of=None) -> dict[str, object]:
        nonlocal market_loads
        market_loads += 1
        return {"status": "ready", "source": "test", "data": {"marker": market_loads}}

    monkeypatch.setattr(production_context_module, "_market_material", load_market_once)

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
    ).build(_pa(take_profit_price=None))

    assert all(
        gate.status is T1GateStatus.INSUFFICIENT_DATA
        for gate in context.scenario_gate_results.values()
    )
    # B 类下一时段分布不依赖止盈/止损价，缺失价格时仍永远计算；只有 C 类首达概率缺失。
    assert all(
        item.first_passage is None
        for item in context.scenario_probabilities_and_gates.values()
    )
    assert any(
        item.opening_distribution is not None
        for item in context.scenario_probabilities_and_gates.values()
    )
    assert "止盈价" in context.materials["probability_chain"]["reason"]
    assert market_loads == 1
    assert context.materials["market_analysis"]["data"]["marker"] == 1


def test_invalid_target_return_is_insufficient_instead_of_preanalysis_failure() -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={
            ("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars
        }
    )

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
    ).build(_pa(take_profit_price=1.0))

    assert all(
        gate.status is T1GateStatus.INSUFFICIENT_DATA
        for gate in context.scenario_gate_results.values()
    )
    assert "C 类首达概率数据不足" in context.materials["probability_chain"]["reason"]


def test_short_trade_plan_with_complete_prices_reaches_first_passage_analysis() -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={
            ("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars
        }
    )

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
    ).build(
        _pa(
            order_direction="做空",
            entry_price=103.0,
            take_profit_price=100.0,
            stop_loss_price=108.0,
        )
    )

    chain = context.materials["probability_chain"]
    assert chain["first_passage"] is not None
    assert "缺少止盈价或止损价" not in str(chain.get("reason") or "")


def test_builder_exposes_three_position_cases_without_user_quantity_input() -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={
            ("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars
        }
    )

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
    ).build(_pa())

    gate = next(iter(context.scenario_gate_results.values()))
    assert context.materials["position_cases"] == {
        "sellable_existing": "已有持仓：当前可按闸门结论处理可卖旧仓",
        "today_locked": "今日锁定：今日买入部分不可卖，下一交易日再按新决策处理",
        "no_position": "通常情况（无持仓）：仅评估是否允许新增买入",
    }
    assert all(action.quantity is None for action in gate.executable_actions)


def test_policy_detector_drives_context_environment_and_evidence(
    tmp_path,
) -> None:
    """软信号（原始新闻文本命中业务词）应驱动 policy_environment 并注入证据。"""
    from src.reasoning.policy_detector import (
        PolicyDetector,
        PolicyDetectorConfig,
        PolicySoftRule,
    )
    from src.hmm_filter import load_config

    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={
            ("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars
        }
    )
    hmm = load_config()
    detectors = hmm["policy_multipliers"]["主力"]

    def build_with_policy(
        material_cache: DailyMaterialCache | None,
        news_items: tuple[NewsItem, ...] = (),
    ):
        detector = PolicyDetector(
            source,
            detectors,
            PolicyDetectorConfig(
                etfs=(),
                volume_lookback_bars=5,
                abnormal_volume_ratio=2.0,
                minimum_confirmations=1,
                history_calendar_days=30,
                ktype="K_DAY",
                soft_rules=(
                    PolicySoftRule("国家队托底中", ("国家队",)),
                    PolicySoftRule("政策打压", ("从严监管",)),
                    PolicySoftRule("政策暖风", ("降准", "稳市场", "政策")),
                ),
            ),
        )
        if material_cache is not None and news_items:
            material_cache.put("news", "半导体", news_items)
        builder = ProductionContextBuilder(
            source,
            today=lambda: datetime(2026, 8, 12).date(),
            history_days=538,
            material_cache=material_cache,
            policy_detector=detector,
        )
        return builder.build(_pa())

    # 原始新闻命中「政策/降准」→ 政策暖风
    warm_cache = DailyMaterialCache(
        tmp_path / "warm_archives", clock=lambda: datetime(2026, 8, 12, 11, 30)
    )
    context = build_with_policy(
        warm_cache,
        (
            NewsItem(
                "国务院出台降准政策支持资本市场",
                "",
                "https://example.test/news",
                "2026-08-12",
            ),
        ),
    )
    assert context.policy_environment == "政策暖风"
    detection = context.materials["policy_detection"]
    assert detection["status"] == "detected"
    assert detection["environment"] == "政策暖风"
    assert any("降准" in item["summary"] for item in detection["evidence"])

    # 无新闻材料 → 回退无干预，且不注入伪证据
    empty_cache = DailyMaterialCache(
        tmp_path / "empty_archives", clock=lambda: datetime(2026, 8, 12, 11, 30)
    )
    context = build_with_policy(empty_cache)
    assert context.policy_environment == "无干预"
    assert context.materials["policy_detection"]["environment"] == "无干预"


def test_policy_detector_default_fallback_keeps_pa_value() -> None:
    """未配置检测器时保持原行为：policy_environment 来自 PA payload。"""
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={
            ("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars
        }
    )
    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
    ).build(_pa(policy_environment="政策暖风"))

    assert context.policy_environment == "政策暖风"
    assert "policy_detection" not in context.materials


def test_limit_pool_material_aggregates_rise_and_fall() -> None:
    from src.data.models import LimitPoolRecord
    from src.integration.production_context import _limit_pool_material

    class Source:
        def get_limit_pool(self, date: str) -> tuple[LimitPoolRecord, ...]:
            return (
                LimitPoolRecord(date, "SZ.000001", 5, "rise"),
                LimitPoolRecord(date, "SZ.000002", 3, "rise"),
                LimitPoolRecord(date, "SZ.000003", 2, "fall"),
            )

    material = _limit_pool_material(Source(), "2026-08-10")

    assert material["status"] == "ready"
    assert material["rise_count"] == 2
    assert material["fall_count"] == 1
    assert material["max_rise_streak"] == 5
    assert material["max_fall_streak"] == 2
    assert len(material["rise_pool"]) == 2
    assert len(material["fall_pool"]) == 1


def test_limit_pool_material_degrades_on_source_error() -> None:
    from src.integration.production_context import _limit_pool_material

    class Source:
        def get_limit_pool(self, date: str):
            raise RuntimeError("network down")

    material = _limit_pool_material(Source(), "2026-08-10")

    assert material["status"] == "source_error"
    assert "network down" in material["error"]


def test_builder_injects_capital_flow_material_from_ledger(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowLedger
    from src.data.models import CapitalFlow

    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars}
    )
    ledger_path = tmp_path / "capital-flow.db"
    with CapitalFlowLedger(ledger_path) as ledger:
        for offset in range(40):
            day = (datetime(2026, 6, 1) + timedelta(days=offset)).date().isoformat()
            ledger.append(CapitalFlow(day, "SZ.000001", 10.0, 5.0, 2.0, -3.0, 3.0))

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
        capital_flow_ledger=ledger_path,
    ).build(_pa())

    material = context.materials["capital_flow"]
    assert material["status"] == "ready"
    assert material["code"] == "SZ.000001"
    assert material["window_days"] == 40
    assert material["main_flow_5d"] == pytest.approx(15.0)
    assert material["main_flow_20d"] == pytest.approx(60.0)
    assert material["latest_main_flow"] == 3.0


def test_builder_reports_not_configured_without_a_ledger() -> None:
    bars = _bars()
    source = FakeMarketDataSource(
        kline_data={("SZ.000001", "K_120M", "2025-02-20", "2026-08-12"): bars}
    )

    context = ProductionContextBuilder(
        source,
        today=lambda: datetime(2026, 8, 12).date(),
        history_days=538,
    ).build(_pa())

    assert context.materials["capital_flow"]["status"] == "not_configured"
