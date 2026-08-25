"""Production bridge from history forecasts to offline Brier reports."""

from __future__ import annotations

import pytest

from src.data.fake_client import FakeMarketDataSource
from src.data.models import Bar
from src.integration.analysis_history import AnalysisHistoryStore


SYMBOL = "SZ.000001"


def _bar(day: str, clock: str, close: float) -> Bar:
    return Bar(
        time_key=f"{day} {clock}:00",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100,
        turnover=1_000.0,
    )


def _payload(
    *, completed_at: str = "2026-08-10T11:30:00", config_version: int = 2
) -> dict:
    rows = [
        ("gap_down", 0.2),
        ("near_reference", 0.5),
        ("gap_up", 0.3),
    ]
    return {
        "input": {
            "symbol": SYMBOL,
            "decision_point": "midday",
            "materials": {
                "sector_analysis": {"sector_name": "半导体"},
                "probability_chain": {
                    "opening_distribution": [
                        {
                            "status": "available",
                            "probability_type": "B",
                            "outcome": outcome,
                            "probability": probability,
                            "prior_weight": 0.1,
                            "config_version": config_version,
                            "decision_point": "午盘",
                            "data_source": "historical_ohlcv:intraday_next_bar",
                        }
                        for outcome, probability in rows
                    ]
                },
            },
        },
        "completed_at": completed_at,
    }


def test_history_append_records_canonical_probability_snapshots(tmp_path) -> None:
    with AnalysisHistoryStore(tmp_path / "history.db") as store:
        assert store.append(_payload()) == 1
        calibration = store.calibration_summary(symbol=SYMBOL)

    assert calibration["prediction_count"] == 3
    assert calibration["resolved_prediction_count"] == 0
    assert calibration["status"] == "insufficient_data"


def test_history_append_uses_frozen_v1_resolution_contract(tmp_path) -> None:
    with AnalysisHistoryStore(tmp_path / "history.db") as store:
        store.append(_payload(config_version=1))
        calibration = store.calibration_summary(symbol=SYMBOL)

    assert calibration["prediction_count"] == 3


def test_history_reconcile_backfills_opening_actuals_and_brier(tmp_path) -> None:
    database = tmp_path / "history.db"
    source = FakeMarketDataSource(
        kline_data={
            (SYMBOL, "K_120M", "2026-08-10", "2026-08-10"): [
                _bar("2026-08-10", "11:30", 100.0),
                _bar("2026-08-10", "15:00", 102.0),
            ]
        }
    )
    with AnalysisHistoryStore(database, calibration_minimum_sample_count=1) as store:
        store.append(_payload())
        assert store.reconcile_calibration(source, as_of="2026-08-10") == 3
        summary = store.calibration_summary(symbol=SYMBOL)

    assert summary["resolved_prediction_count"] == 3
    reports = {item["outcome"]: item for item in summary["reports"]}
    assert reports["gap_up"]["brier_score"] == pytest.approx(0.49)
    assert reports["gap_up"]["prior_adjustment_direction"] == "increase"
    assert reports["near_reference"]["prior_adjustment_direction"] == "decrease"


def test_close_prediction_resolves_from_next_trading_days_first_bar(tmp_path) -> None:
    database = tmp_path / "history.db"
    payload = _payload(completed_at="2026-08-10T15:00:00")
    payload["input"]["decision_point"] = "close"
    for row in payload["input"]["materials"]["probability_chain"]["opening_distribution"]:
        row["decision_point"] = "收盘"
        row["data_source"] = "historical_ohlcv:overnight_next_bar"
    source = FakeMarketDataSource(
        kline_data={
            (SYMBOL, "K_120M", "2026-08-10", "2026-08-11"): [
                _bar("2026-08-10", "11:30", 100.0),
                _bar("2026-08-10", "15:00", 100.0),
                _bar("2026-08-11", "11:30", 98.0),
                _bar("2026-08-11", "15:00", 99.0),
            ]
        }
    )
    with AnalysisHistoryStore(database, calibration_minimum_sample_count=1) as store:
        record_id = store.append(payload)
        assert store.reconcile_calibration(source, as_of="2026-08-11") == 3
        reports = {
            item["outcome"]: item
            for item in store.calibration_summary(symbol=SYMBOL)["reports"]
        }
        assert reports["gap_down"]["prior_adjustment_direction"] == "increase"
        assert store.delete(record_id) is True
        assert store.calibration_summary(symbol=SYMBOL)["prediction_count"] == 0
