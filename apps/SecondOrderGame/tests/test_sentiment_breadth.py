from datetime import datetime

from src.data.sentiment_breadth import SentimentBreadthCalculator
from src.data.sentiment_ledger import SentimentState


def test_breadth_aggregates_registered_sectors_only():
    states = (
        SentimentState("SH.BK1", 20, datetime(2026, 8, 14)),
        SentimentState("SH.BK2", 60, datetime(2026, 8, 14)),
        SentimentState("SH.BK3", 99, datetime(2026, 8, 14)),
    )

    result = SentimentBreadthCalculator().calculate(
        states,
        registered_sector_codes=("SH.BK1", "SH.BK2"),
        cycle_positions={"SH.BK1": "高潮", "SH.BK2": "发酵"},
    )

    assert result.status == "complete"
    assert result.sector_count == 2
    assert result.median_sentiment == 40
    assert result.climax_ratio == 0.5


def test_breadth_never_claims_complete_without_a_frozen_registry():
    states = (SentimentState("SH.BK1", 50, datetime(2026, 8, 14)),)

    result = SentimentBreadthCalculator().calculate(
        states,
        registered_sector_codes=("SH.BK1",),
        cycle_positions={"SH.BK1": "发酵"},
        registry_complete=False,
    )

    assert result.status == "partial"
    assert result.climax_ratio is None
