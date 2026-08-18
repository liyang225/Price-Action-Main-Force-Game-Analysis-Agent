from datetime import datetime

from src.data.models import ModelNewsJudgment, NewsItem
from src.data.news_sentiment import NewsSentimentAnalyzer


def test_news_analysis_scores_context_and_deduplicates_one_event():
    analyzer = NewsSentimentAnalyzer.from_file()
    duplicate = NewsItem(
        "芯片订单增长不及预期",
        "公司公告称需求低于预期",
        "https://example.test/a",
        "2026-08-13 10:00:00",
        "交易所公告",
        ("SZ.000001",),
    )

    events = analyzer.analyze(
        (duplicate, duplicate),
        sector_code="SH.BK0001",
        sector_name="半导体",
        target_security="SZ.000001",
        as_of=datetime(2026, 8, 14, 11, 30),
        subject_purpose={"true_purpose": "借利好出货"},
    )

    assert len(events) == 1
    assert events[0].sentiment_score < 0
    assert events[0].relevance == 1
    assert events[0].source_credibility == 1
    assert events[0].event_fingerprint


def test_expired_news_is_invalid_and_cannot_move_sentiment():
    analyzer = NewsSentimentAnalyzer.from_file()
    events = analyzer.analyze(
        (NewsItem("重大利好", "行业景气回升", "", "2026-07-01", "", ()),),
        sector_code="SH.BK0001",
        sector_name="半导体",
        target_security="SZ.000001",
        as_of=datetime(2026, 8, 14, 11, 30),
    )

    assert events[0].validity == 0
    assert events[0].sentiment_score == 0


def test_futu_m_d_publish_time_borrows_reference_year() -> None:
    analyzer = NewsSentimentAnalyzer.from_file()
    events = analyzer.analyze(
        (NewsItem("重大利好", "行业景气回升", "", "8/13", "", ()),),
        sector_code="SH.BK0001",
        sector_name="半导体",
        target_security="SZ.000001",
        as_of=datetime(2026, 8, 14, 11, 30),
    )

    # "8/13" has no year; it must borrow the reference year instead of failing
    # freshness parsing and collapsing the score to zero.
    assert events[0].validity > 0
    assert events[0].sentiment_score > 0


def test_tavily_rfc2822_publish_time_keeps_validity_and_sentiment() -> None:
    """Tavily emits RFC 2822 dates (e.g. "Sat, 15 Aug 2026 00:00:00 GMT").

    A failed parse used to collapse validity to zero and wipe every model
    sentiment score; regression guard for that path.
    """
    analyzer = NewsSentimentAnalyzer.from_file()
    events = analyzer.analyze(
        (
            NewsItem(
                "费城半导体指数日内跌1.21%",
                "博通下跌6%",
                "",
                "Fri, 14 Aug 2026 07:35:42 GMT",
                "Tavily",
                (),
            ),
        ),
        sector_code="SH.BK0001",
        sector_name="半导体",
        target_security="SZ.000001",
        as_of=datetime(2026, 8, 15, 9, 30),
        subject_purpose={"true_purpose": "产业扶持"},
        model_judgments=(ModelNewsJudgment(sentiment_score=-0.5),),
    )

    assert events[0].validity > 0
    assert events[0].sentiment_score < 0


def test_model_relevance_and_credibility_override_deterministic_factors() -> None:
    analyzer = NewsSentimentAnalyzer.from_file()
    events = analyzer.analyze(
        (
            NewsItem(
                "半导体板块景气回升",
                "行业景气回升",
                "",
                "2026-08-14 10:00:00",
                "Tavily",
                (),
            ),
        ),
        sector_code="SH.BK0001",
        sector_name="半导体",
        target_security="SZ.000001",
        as_of=datetime(2026, 8, 14, 11, 30),
        subject_purpose={"true_purpose": "产业扶持"},
        model_judgments=(
            ModelNewsJudgment(
                sentiment_score=4.0,
                relevance=1.0,
                credibility=0.85,
            ),
        ),
    )

    # Tavily has no related securities and a generic source, so the
    # deterministic defaults would be relevance=0.7 / credibility=0.65; the
    # model-supplied factors must win instead.
    assert events[0].relevance == 1.0
    assert events[0].source_credibility == 0.85
    assert events[0].sentiment_score > 0
