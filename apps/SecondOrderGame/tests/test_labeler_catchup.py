"""Tests for the startup labeler catch-up sweep."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from src.data.fake_client import FakeMarketDataSource
from src.data.models import Bar
from src.labeler.ledger import LabelLedger
from src.labeler.nightly import (
    CATCHUP_LABEL_WINDOW,
    compute_labeler_gap,
    run_labeler_catchup,
)
from src.labeler.sector_labeler import SectorLabeler


def _bars(day_count: int = 120, *, start: date) -> list[Bar]:
    bars: list[Bar] = []
    price = 100.0
    for index in range(day_count):
        day = start + timedelta(days=index)
        if day.weekday() >= 5:
            continue
        trend = 0.012 if index < day_count * 2 // 3 else -0.012
        open_ = price
        close = open_ * (1 + trend + (index % 5) * 0.001)
        high = max(open_, close) * 1.01
        low = min(open_, close) * 0.99
        volume = 1_000_000 + index * 1000
        bars.append(
            Bar(
                time_key=day.isoformat(),
                open=round(open_, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=volume,
                turnover=round(volume * close, 2),
            )
        )
        price = close
    return bars


class _FlexibleSource:
    """Returns the same bars for any kline request; ignores parameter keys."""

    def __init__(self) -> None:
        self._sector_bars = _bars(start=date(2026, 3, 1))
        self._stock_bars = _bars(start=date(2026, 3, 1))

    def get_kline(self, code: str, ktype: str, start: str, end: str) -> list[Bar]:
        return list(self._sector_bars if code == "BK0475" else self._stock_bars)

    def get_sector_constituents(self, sector_code: str) -> tuple[str, ...]:
        return ("SZ.000001",) if sector_code == "BK0475" else ()


def _fake_source() -> _FlexibleSource:
    return _FlexibleSource()


def test_compute_labeler_gap_empty_ledger_reports_missed(tmp_path) -> None:
    rule_hash = SectorLabeler().rule_hash
    with LabelLedger(tmp_path / "labels.db") as ledger:
        caught_up, missed = compute_labeler_gap(
            ledger,
            sectors={"BK0475": "半导体"},
            as_of="2026-08-10",
            rule_hash=rule_hash,
        )
    assert caught_up is None
    assert len(missed) == 1  # the cutoff day itself


def test_compute_labeler_gap_skips_fresh_sector(tmp_path) -> None:
    rule_hash = SectorLabeler().rule_hash
    with LabelLedger(tmp_path / "labels.db") as ledger:
        ledger.record_sector_labels(
            "BK0475",
            [
                {
                    "date": "2026-08-10",
                    "cycle_position": "发酵",
                    "status": "labeled",
                    "rule_hash": rule_hash,
                    "config_version": 1,
                }
            ],
        )
        caught_up, missed = compute_labeler_gap(
            ledger,
            sectors={"BK0475": "半导体"},
            as_of="2026-08-10",
            rule_hash=rule_hash,
            label_window_days=5,
        )
    assert caught_up == "2026-08-10"
    assert missed == ()


def test_run_labeler_catchup_no_gap_is_noop(tmp_path) -> None:
    source = _fake_source()
    rule_hash = SectorLabeler().rule_hash
    ledger_path = tmp_path / "labels.db"
    with LabelLedger(ledger_path) as ledger:
        # Fresh sector: latest labeled day is today.
        ledger.record_sector_labels(
            "BK0475",
            [
                {
                    "date": "2026-08-10",
                    "cycle_position": "发酵",
                    "status": "labeled",
                    "rule_hash": rule_hash,
                    "config_version": 1,
                }
            ],
        )
    report = run_labeler_catchup(
        source,
        trading_date="2026-08-10",
        sectors={"BK0475": "半导体"},
        ledger_database=ledger_path,
        shadow_database=tmp_path / "shadow.db",
        production_directory=tmp_path / "production",
        report_directory=tmp_path / "reports",
        confusion_database=None,
        label_window_days=5,
    )
    assert report.ran_sweeps == 0
    assert report.missed_dates == ()
    assert report.errors == ()


def test_run_labeler_catchup_backfills_missed_dates(tmp_path) -> None:
    source = _fake_source()
    ledger_path = tmp_path / "labels.db"
    # Empty ledger -> the sweep must run at least one catch-up day.
    report = run_labeler_catchup(
        source,
        trading_date="2026-08-10",
        sectors={"BK0475": "半导体"},
        ledger_database=ledger_path,
        shadow_database=tmp_path / "shadow.db",
        production_directory=tmp_path / "production",
        report_directory=tmp_path / "reports",
        confusion_database=None,
        label_window_days=5,
    )
    assert report.ran_sweeps >= 1
    assert report.errors == ()
    with LabelLedger(ledger_path) as ledger:
        rows = ledger.sector_labels("BK0475")
        assert rows  # at least one sector day was recorded
        for row in rows:
            assert row.trading_date <= "2026-08-10"
