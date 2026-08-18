from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import pytest

from src.data.sentiment_ledger import SentimentLedger, SentimentLedgerError, SentimentState


def test_first_save_creates_a_sector_state_and_returns_it(temp_db) -> None:
    ledger = SentimentLedger(temp_db)
    updated_at = datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc)

    saved = ledger.save(
        SentimentState(
            sector_code="BK001",
            sentiment_index=62.5,
            updated_at=updated_at,
        )
    )

    assert saved == SentimentState(
        sector_code="BK001",
        sentiment_index=62.5,
        updated_at=updated_at,
    )
    assert ledger.load("BK001") == saved


def test_saving_a_sector_again_replaces_only_that_sector_state(temp_db) -> None:
    ledger = SentimentLedger(temp_db)
    original = SentimentState("BK001", 42.0, datetime(2026, 8, 9, tzinfo=timezone.utc))
    replacement = SentimentState("BK001", 58.0, datetime(2026, 8, 10, tzinfo=timezone.utc))
    other_sector = SentimentState("BK002", 35.0, datetime(2026, 8, 10, tzinfo=timezone.utc))

    ledger.save(original)
    ledger.save(other_sector)
    ledger.save(replacement)

    assert ledger.load("BK001") == replacement
    assert ledger.load("BK002") == other_sector


def test_closing_and_reopening_restores_the_same_sector_state(tmp_path) -> None:
    database_path = tmp_path / "sentiment-ledger.sqlite"
    expected = SentimentState(
        sector_code="BK001",
        sentiment_index=71.25,
        updated_at=datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc),
    )

    with SentimentLedger(database_path) as ledger:
        ledger.save(expected)

    with SentimentLedger(database_path) as reopened:
        assert reopened.load("BK001") == expected


def test_unknown_sector_has_no_persisted_state(temp_db) -> None:
    ledger = SentimentLedger(temp_db)

    assert ledger.load("UNKNOWN") is None


@pytest.mark.parametrize(
    "sentiment_index",
    [-0.01, 100.01, float("nan"), float("inf")],
)
def test_sentiment_index_must_be_a_finite_value_in_the_configured_range(
    temp_db, sentiment_index: float
) -> None:
    ledger = SentimentLedger(temp_db)

    with pytest.raises(ValueError, match="sentiment_index"):
        ledger.save(
            SentimentState(
                "BK001",
                sentiment_index,
                datetime(2026, 8, 10, tzinfo=timezone.utc),
            )
        )


def test_malformed_persisted_state_fails_loudly(temp_db) -> None:
    ledger = SentimentLedger(temp_db)
    ledger.save(SentimentState("BK001", 50.0, datetime(2026, 8, 10, tzinfo=timezone.utc)))

    temp_db.execute(
        "UPDATE sector_sentiment_ledger SET sentiment_index = ? WHERE sector_code = ?",
        ("not-a-number", "BK001"),
    )
    temp_db.commit()

    with pytest.raises(SentimentLedgerError, match="BK001"):
        ledger.load("BK001")


def test_incompatible_existing_schema_is_not_silently_replaced(tmp_path) -> None:
    database_path = tmp_path / "incompatible.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE sector_sentiment_ledger (sector_code TEXT PRIMARY KEY)")

    with pytest.raises(SentimentLedgerError, match="incompatible"):
        SentimentLedger(database_path)
