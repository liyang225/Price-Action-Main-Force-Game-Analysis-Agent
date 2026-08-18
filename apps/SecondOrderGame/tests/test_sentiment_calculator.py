from datetime import datetime
from src.data.models import NewsItem
from src.data.sentiment_calculator import SentimentCalculator


def test_news_from_material_cache_changes_sector_index_and_respects_daily_caps():
    calculator = SentimentCalculator(
        {
            "version": 1,
            "index_source": "futu_industry_weighted",
            "range": {"min": 0, "baseline": 50, "max": 100},
            "quota": {"daily_net": 15, "news": 8, "price_action": 15, "single_news": 8},
            "news_scoring": {
                "max_age_days": 7,
                "half_life_days": 3,
                "default_relevance": 0.7,
                "unrelated_relevance": 0.25,
                "default_source_credibility": 0.65,
                "major_media_credibility": 0.85,
                "authoritative_source_credibility": 1.0,
            },
            "inertia": {"decay": 0.9, "apply_decay_on_no_news_days": True, "apply_price_action_on_no_news_days": True},
            "major_move_suppression": {
                "triggers": {"single_day_drop": -0.05, "two_day_cumulative": -0.08},
                "news_quota_multiplier": 0.4,
                "price_action_unaffected": True,
                "bidirectional": True,
            },
        }
    )
    result = calculator.calculate(
        sector_code="半导体",
        previous_index=50,
        news=(NewsItem("芯片需求增长，政策支持", "行业景气回升", "", "2026-08-14"),),
        price_action={"daily_return": 0.02},
        updated_at=datetime(2026, 8, 14),
    )

    assert result.sentiment_index > 50
    assert result.news_delta > 0
    assert result.daily_delta <= 15
    assert result.sentiment_index == 62.0


def test_price_action_weight_is_doubled_and_capped_at_fifteen():
    calculator = SentimentCalculator.from_file()

    doubled = calculator.calculate(
        sector_code="SH.BK0001",
        previous_index=50,
        news=(),
        price_action={"daily_return": 0.02},
        updated_at=datetime(2026, 8, 14),
    )
    capped_up = calculator.calculate(
        sector_code="SH.BK0001",
        previous_index=50,
        news=(),
        price_action={"daily_return": 0.10},
        updated_at=datetime(2026, 8, 14),
    )
    capped_down = calculator.calculate(
        sector_code="SH.BK0001",
        previous_index=50,
        news=(),
        price_action={"daily_return": -0.10},
        updated_at=datetime(2026, 8, 14),
    )

    assert doubled.price_action_delta == 4
    assert capped_up.price_action_delta == 15
    assert capped_down.price_action_delta == -15


def test_major_drop_reduces_news_quota_but_keeps_price_action():
    calculator = SentimentCalculator.from_file()
    result = calculator.calculate(
        sector_code="半导体",
        previous_index=60,
        news=(NewsItem("重大利好", "", "", "2026-08-14"),),
        price_action={"daily_return": -0.06},
        updated_at=datetime(2026, 8, 14),
    )

    assert result.major_move_suppressed is True
    assert result.news_delta <= 3.2
    assert result.price_action_delta < 0


def test_single_news_quota_is_config_driven():
    config = SentimentCalculator.from_file().config
    config["quota"]["single_news"] = 2
    calculator = SentimentCalculator(config)

    result = calculator.calculate(
        sector_code="SH.BK0001",
        previous_index=50,
        news=({"title": "event", "sentiment_score": 8.0},),
        price_action={},
        updated_at=datetime(2026, 8, 14),
    )

    assert result.news_delta == 2


def test_configured_baseline_is_exposed_and_used_for_first_record():
    calculator = SentimentCalculator.from_file()

    result = calculator.calculate(
        sector_code="SH.BK0001",
        previous_index=None,
        news=(),
        price_action={},
        updated_at=datetime(2026, 8, 14),
    )

    assert calculator.baseline == 50
    assert result.previous_index == calculator.baseline


def test_result_exposes_auditable_formula():
    calculator = SentimentCalculator.from_file()

    result = calculator.calculate(
        sector_code="SH.BK0001",
        previous_index=50,
        news=(),
        price_action={},
        updated_at=datetime(2026, 8, 14),
    )

    assert "指数 = clamp" in result.formula
    assert "当日净增量 = clamp" in result.formula
    assert "消息增量 = clamp" in result.formula
    assert "行情增量 = clamp" in result.formula
    assert "日收益率 × 200" in result.formula
    assert "情绪分 = 大模型评分" in result.formula
    assert result.to_dict()["formula"] == result.formula
