from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Sequence

import pytest
import yaml

from src.data.daily_cache import DailyMaterialSnapshot
from src.data.fake_client import FakeMarketDataSource
from src.data.models import Bar, NewsItem
from src.data.protocol import DataSourceError
from src.hmm_filter import HMMFilter
from src.reasoning.policy_detector import (
    PolicyDetector,
    PolicyDetectorConfig,
    PolicySoftRule,
    VerifiedETF,
    load_policy_detector_config,
)


CONFIG_PATH = Path(__file__).parent.parent / "config" / "hmm_prior.yaml"
DETECTOR_CONFIG_PATH = (
    Path(__file__).parent.parent / "config" / "policy_detector.yaml"
)


def _policy_multipliers() -> dict[str, dict[str, float]]:
    with CONFIG_PATH.open(encoding="utf-8") as config_file:
        return yaml.safe_load(config_file)["policy_multipliers"]["主力"]


def _news_material(
    items: Sequence[NewsItem | str], *, sector: str = "半导体"
) -> DailyMaterialSnapshot:
    """原始新闻缓存材料：软信号直接扫描 title + snippet 文本。"""
    return DailyMaterialSnapshot(
        date(2026, 8, 11),
        {"news": {sector: tuple(items)}},
    )


def _policy_news(text: str, *, snippet: str = "") -> NewsItem:
    return NewsItem(
        title=text,
        snippet=snippet,
        url="https://example.test/news",
        published_date="2026-08-11",
    )


def _bars(latest_volume: int) -> list[Bar]:
    return [
        Bar(f"2026-08-{day:02d}", 100, 101, 99, 100, 100, 10_000)
        for day in range(6, 11)
    ] + [Bar("2026-08-11", 100, 102, 99, 101, latest_volume, 20_000)]


class KlineSource:
    def __init__(
        self,
        bars_by_code: dict[str, Sequence[Bar]],
        *,
        failures: set[str] | None = None,
    ) -> None:
        self.bars_by_code = bars_by_code
        self.failures = failures or set()
        self.requested_codes: list[str] = []

    def get_kline(self, code: str, ktype: str, start: str, end: str) -> Sequence[Bar]:
        self.requested_codes.append(code)
        if code in self.failures:
            raise DataSourceError(f"{code} unavailable")
        return self.bars_by_code.get(code, ())

    def get_capital_flow(self, code: str, date: str):  # pragma: no cover - protocol only
        raise NotImplementedError

    def search_news(self, keyword: str):  # pragma: no cover - decision path uses cache
        raise AssertionError("policy detection must not search news synchronously")

    def get_dragon_tiger(self, code: str, date: str):  # pragma: no cover - protocol only
        raise NotImplementedError


def _detector(
    source=None,
    *,
    etfs: tuple[VerifiedETF, ...] = (),
    minimum_confirmations: int = 1,
) -> PolicyDetector:
    return PolicyDetector(
        source or FakeMarketDataSource(),
        _policy_multipliers(),
        PolicyDetectorConfig(
            etfs=etfs,
            volume_lookback_bars=5,
            abnormal_volume_ratio=2.0,
            minimum_confirmations=minimum_confirmations,
            history_calendar_days=30,
            ktype="K_DAY",
            soft_rules=(
                PolicySoftRule("国家队托底中", ("中央汇金", "国家队", "增持ETF", "ETF增持", "托底", "平准基金")),
                PolicySoftRule("政策打压", ("从严监管", "政策打压")),
                PolicySoftRule("政策暖风", ("支持资本市场", "政策支持", "降准", "降息", "稳市场", "稳定市场", "降税", "提振", "增量资金")),
            ),
            policy_news_keys=("半导体", "白酒"),
        ),
    )


def test_cached_raw_news_selects_warm_multiplier_group() -> None:
    """软信号直接扫原始新闻文本，命中业务词即选暖风组。"""
    snapshot = _news_material((_policy_news("国务院宣布降准释放流动性支持实体经济"),))
    multipliers = _policy_multipliers()
    detector = _detector()

    result = detector.detect(snapshot)

    assert result.environment == "政策暖风"
    assert dict(result.multipliers) == multipliers["政策暖风"]
    assert result.soft_branch_enabled is True
    assert result.hard_branch_enabled is False
    assert any("降准" in item.summary for item in result.evidence)


def test_snippet_text_is_also_scanned() -> None:
    """软信号同时扫描 snippet，业务词出现在摘要中同样命中。"""
    snapshot = _news_material(
        (_policy_news("央行操作", snippet="央行今日实施降准，释放长期资金"),)
    )

    result = _detector().detect(snapshot)

    assert result.environment == "政策暖风"
    assert any("降准" in item.summary for item in result.evidence)


@pytest.mark.parametrize(
    ("news_text", "expected"),
    [
        ((), "无干预"),
        ("国务院发布降准政策支持经济", "政策暖风"),
        ("中央汇金宣布增持ETF稳定市场", "国家队托底中"),
        ("监管部门从严监管并限制高风险交易", "政策打压"),
    ],
    ids=["none", "warm", "state_support", "crackdown"],
)
def test_raw_news_text_can_select_all_soft_policy_outcomes(
    news_text: str | tuple[()], expected: str
) -> None:
    snapshot = (
        _news_material((_policy_news(news_text),))
        if news_text
        else DailyMaterialSnapshot(date(2026, 8, 11), {})
    )
    result = _detector().detect(snapshot)

    assert result.environment == expected
    assert dict(result.multipliers) == _policy_multipliers()[expected]


def test_user_material_can_select_all_soft_policy_outcomes() -> None:
    """user_material 注入新闻文本时同样按业务词选组。"""
    cases = (
        (("国务院发布降准政策支持经济",), "政策暖风"),
        (("中央汇金宣布增持ETF稳定市场",), "国家队托底中"),
        (("监管部门从严监管并限制高风险交易",), "政策打压"),
    )
    for material, expected in cases:
        result = _detector().detect(
            DailyMaterialSnapshot(date(2026, 8, 11), {}),
            user_material=material,
        )
        assert result.environment == expected
        assert dict(result.multipliers) == _policy_multipliers()[expected]


def test_missing_news_disables_soft_branch_instead_of_inventing() -> None:
    result = _detector().detect(DailyMaterialSnapshot(date(2026, 8, 11), {}))

    assert result.environment == "无干预"
    assert result.soft_branch_enabled is False
    assert not [item for item in result.evidence if item.channel == "soft"]


def test_sector_not_in_policy_news_keys_is_skipped() -> None:
    """policy_news_keys 限定扫描板块；未列出的板块新闻不参与软信号。"""
    snapshot = DailyMaterialSnapshot(
        date(2026, 8, 11),
        {"news": {"光伏": (_policy_news("国家出台降准政策支持经济"),)}},
    )

    result = _detector().detect(snapshot)

    assert result.environment == "无干预"
    assert result.soft_branch_enabled is False


def test_decision_wrapper_news_items_are_flattened() -> None:
    """决策视图把新闻包装成 {status, items, ...}，检测器应能展开扫描。"""
    snapshot = DailyMaterialSnapshot(
        date(2026, 8, 11),
        {
            "news": {
                "半导体": {
                    "status": "ready",
                    "items": [
                        {"title": "央行降准释放流动性", "snippet": ""},
                        {"title": "企业日常经营动态", "snippet": ""},
                    ],
                }
            }
        },
    )

    result = _detector().detect(snapshot)

    assert result.environment == "政策暖风"
    assert any("降准" in item.summary for item in result.evidence)


def test_verified_broad_market_etf_abnormal_volume_is_an_explainable_hard_signal() -> None:
    etfs = (
        VerifiedETF("ETF.CSI300.VERIFIED", "沪深300"),
        VerifiedETF("ETF.SSE50.VERIFIED", "上证50"),
    )
    source = KlineSource({etf.code: _bars(250) for etf in etfs})

    result = _detector(
        source, etfs=etfs, minimum_confirmations=2
    ).detect(DailyMaterialSnapshot(date(2026, 8, 11), {}))

    assert result.environment == "国家队托底中"
    assert source.requested_codes == [etf.code for etf in etfs]
    assert len([item for item in result.evidence if item.channel == "hard"]) == 2
    assert all("成交量为历史中位数" in item.summary for item in result.evidence)


def test_hard_market_evidence_wins_over_conflicting_soft_news() -> None:
    etf = VerifiedETF("ETF.CSI300.VERIFIED", "沪深300")
    source = KlineSource({etf.code: _bars(250)})

    result = _detector(source, etfs=(etf,)).detect(
        DailyMaterialSnapshot(date(2026, 8, 11), {}),
        user_material=("监管部门从严监管并限制高风险交易",),
    )

    assert result.environment == "国家队托底中"
    assert {item.environment for item in result.evidence} >= {
        "国家队托底中",
        "政策打压",
    }
    assert any("硬信号优先" in item.summary for item in result.evidence)


def test_data_source_failure_is_explained_and_soft_evidence_still_works() -> None:
    etf = VerifiedETF("ETF.CSI300.VERIFIED", "沪深300")
    source = KlineSource({}, failures={etf.code})

    result = _detector(source, etfs=(etf,)).detect(
        DailyMaterialSnapshot(date(2026, 8, 11), {}),
        user_material=("国务院发布降准政策支持经济",),
    )

    assert result.environment == "政策暖风"
    assert result.hard_branch_enabled is True
    assert any("unavailable" in item.summary for item in result.evidence)


def test_policy_detection_and_multiplier_selection_do_not_reset_hmm_belief() -> None:
    with CONFIG_PATH.open(encoding="utf-8") as config_file:
        hmm_config = yaml.safe_load(config_file)
    filter_ = HMMFilter(hmm_config)
    filter_.update("高潮")
    belief_before = filter_.belief

    detection = _detector().detect(
        DailyMaterialSnapshot(date(2026, 8, 11), {}),
        user_material=("国务院发布降准政策支持经济",),
    )
    distribution = filter_.predict_behaviors("主力", policy=detection.environment)

    assert detection.environment == "政策暖风"
    assert filter_.belief == belief_before
    assert sum(distribution.values()) == pytest.approx(1.0)


def test_unverified_or_impossible_etf_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="code"):
        VerifiedETF("", "沪深300")
    with pytest.raises(ValueError, match="minimum_confirmations"):
        PolicyDetectorConfig(
            etfs=(VerifiedETF("ETF.CSI300.VERIFIED", "沪深300"),),
            volume_lookback_bars=5,
            abnormal_volume_ratio=2.0,
            minimum_confirmations=2,
            history_calendar_days=30,
            ktype="K_DAY",
            soft_rules=(
                PolicySoftRule("国家队托底中", ("国家队",)),
                PolicySoftRule("政策打压", ("政策打压",)),
                PolicySoftRule("政策暖风", ("降准",)),
            ),
        )


def test_load_policy_detector_config_from_yaml() -> None:
    config = load_policy_detector_config(DETECTOR_CONFIG_PATH)

    assert config.etfs
    assert {etf.benchmark for etf in config.etfs} == {"沪深300", "上证50"}
    assert config.volume_lookback_bars >= 2
    assert config.abnormal_volume_ratio > 1
    assert config.minimum_confirmations >= 1
    assert {rule.environment for rule in config.soft_rules} == {
        "政策暖风",
        "国家队托底中",
        "政策打压",
    }
    # 用户指定固定检测词必须全部出现在软信号词表中
    warm_keywords = next(
        rule.keywords for rule in config.soft_rules if rule.environment == "政策暖风"
    )
    assert {"政策", "国家", "政府", "中央", "降准", "降息", "稳市场"} <= set(warm_keywords)
    # 暖风业务词写广写全：政策工具类、流动性类、信心类、增长类全覆盖
    assert {"降税", "释放流动性", "提振信心", "增量资金", "稳增长", "专项债", "特别国债"} <= set(warm_keywords)
    # 政策打压基本不存在：词表精简到极少量
    crackdown_keywords = next(
        rule.keywords for rule in config.soft_rules if rule.environment == "政策打压"
    )
    assert len(crackdown_keywords) <= 3


def test_build_policy_detector_uses_production_config() -> None:
    from src.reasoning.policy_detector import build_policy_detector

    detector = build_policy_detector(
        FakeMarketDataSource(), root=Path(__file__).parent.parent
    )
    detection = detector.detect(
        DailyMaterialSnapshot(date(2026, 8, 11), {}),
        user_material=("国务院发布降准政策支持经济",),
    )
    assert detection.environment == "政策暖风"
