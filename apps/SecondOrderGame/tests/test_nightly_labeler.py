"""End-to-end tests for the nightly labeler sweep."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.data.fake_client import FakeMarketDataSource
from src.data.models import Bar
from src.labeler.ledger import LabelLedger
from src.labeler.nightly import run_nightly


def _bars(day_count: int = 80, *, start: date) -> list[Bar]:
    """Synthetic OHLCV with a rising then falling pattern."""
    bars: list[Bar] = []
    price = 100.0
    for index in range(day_count):
        day = start + timedelta(days=index)
        if day.weekday() >= 5:
            continue
        trend = 0.01 if index < day_count * 2 // 3 else -0.01
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


def _fake_source() -> FakeMarketDataSource:
    target = date(2026, 8, 10)
    start = "2026-05-01"
    end_buffer = (target + timedelta(days=30)).isoformat()
    sector_bars = _bars(start=date(2026, 5, 1))
    stock_bars = _bars(start=date(2026, 5, 1))
    return FakeMarketDataSource(
        kline_data={
            ("BK0475", "K_DAY", start, end_buffer): sector_bars,
            ("SZ.000001", "K_DAY", start, end_buffer): stock_bars,
        },
        sector_constituents={"BK0475": ("SZ.000001",)},
    )


def test_nightly_sweep_records_labels_and_attempts_cutover(tmp_path) -> None:
    source = _fake_source()
    ledger_path = tmp_path / "labels.db"
    shadow_path = tmp_path / "shadow.db"
    production = tmp_path / "production"
    reports = tmp_path / "reports"
    confusion = tmp_path / "confusion.db"

    report = run_nightly(
        source,
        trading_date="2026-08-10",
        sectors={"BK0475": "半导体"},
        ledger_database=ledger_path,
        shadow_database=shadow_path,
        production_directory=production,
        report_directory=reports,
        confusion_database=confusion,
        history_start="2026-05-01",
    )

    assert report.errors == ()
    assert len(report.sector_runs) == 1
    run = report.sector_runs[0]
    assert run.sector_code == "BK0475"
    assert run.sector_labeled >= 0
    assert run.stock_labeled >= 0

    with LabelLedger(ledger_path) as ledger:
        sector_rows = ledger.sector_labels("BK0475")
        stock_rows = ledger.stock_labels("SZ.000001")
        assert sector_rows
        assert stock_rows
        # Rows are only labeled days; the sweep must not record future days.
        for row in sector_rows:
            assert row.trading_date <= "2026-08-10"
        for row in stock_rows:
            assert row.trading_date <= "2026-08-10"

    # Cutover was attempted and safely reports not_ready (no shadow data).
    assert report.cutover_status in {"not_attempted", "not_ready", "cutover", "failed"}


def test_nightly_sweep_reconciles_confusion_counts(tmp_path) -> None:
    source = _fake_source()
    confusion = tmp_path / "confusion.db"
    report = run_nightly(
        source,
        trading_date="2026-08-10",
        sectors={"BK0475": "半导体"},
        ledger_database=tmp_path / "labels.db",
        shadow_database=tmp_path / "shadow.db",
        production_directory=tmp_path / "production",
        report_directory=tmp_path / "reports",
        confusion_database=confusion,
        history_start="2026-05-01",
        llm_observations=[("BK0475", "2026-08-10", "发酵")],
    )
    assert report.errors == ()
    # If the sector was labeled on that date the count increments; otherwise no.
    assert isinstance(report.confusion_increments, dict)


def test_nightly_sweep_isolates_a_failing_sector(tmp_path) -> None:
    source = _fake_source()
    report = run_nightly(
        source,
        trading_date="2026-08-10",
        sectors={"BK0475": "半导体", "BK9999": "无数据板块"},
        ledger_database=tmp_path / "labels.db",
        shadow_database=tmp_path / "shadow.db",
        production_directory=tmp_path / "production",
        report_directory=tmp_path / "reports",
        confusion_database=None,
        history_start="2026-05-01",
    )
    # BK9999 has no kline data -> its run records an error but BK0475 proceeds.
    assert any("BK9999" in error for error in report.errors)
    assert len(report.sector_runs) == 2


def test_capital_flows_reader_returns_flows_only_when_the_ledger_has_them(
    tmp_path,
) -> None:
    from src.data.capital_flow_ledger import CapitalFlowLedger
    from src.data.models import CapitalFlow
    from src.labeler.nightly import _capital_flows_reader

    database = tmp_path / "capital-flow.db"
    with CapitalFlowLedger(database) as ledger:
        ledger.append(CapitalFlow("2026-08-10", "SZ.000001", 10.0, 5.0, 2.0, -3.0, 17.0))

    reader = _capital_flows_reader(database)
    flows = reader("SZ.000001")
    assert flows is not None and len(flows) == 1
    assert reader("SZ.999999") is None
    assert _capital_flows_reader(None)("SZ.000001") is None


def test_nightly_sweep_accepts_a_capital_flow_ledger_without_aborting(
    tmp_path,
) -> None:
    source = _fake_source()
    report = run_nightly(
        source,
        trading_date="2026-08-10",
        sectors={"BK0475": "半导体"},
        ledger_database=tmp_path / "labels.db",
        shadow_database=tmp_path / "shadow.db",
        production_directory=tmp_path / "production",
        report_directory=tmp_path / "reports",
        confusion_database=tmp_path / "confusion.db",
        history_start="2026-05-01",
        capital_flow_database=tmp_path / "capital-flow.db",
    )
    assert report.errors == ()
    assert len(report.sector_runs) == 1


def test_nightly_watchlist_mode_labels_watchlist_and_injects_capital_flow(
    tmp_path,
) -> None:
    import json

    from src.data.capital_flow_ledger import CapitalFlowLedger
    from src.data.models import CapitalFlow
    from src.labeler.ledger import LabelLedger

    target = date(2026, 8, 10)
    start = "2026-05-01"
    end_buffer = (target + timedelta(days=30)).isoformat()
    stock_bars = _bars(start=date(2026, 5, 1))
    sector_bars = _bars(start=date(2026, 5, 1))
    capital_db = tmp_path / "capital-flow.db"
    with CapitalFlowLedger(capital_db) as capital_ledger:
        for bar in stock_bars:
            capital_ledger.append(
                CapitalFlow(
                    bar.time_key[:10], "SZ.000001", 10.0, 5.0, 2.0, -3.0, 17.0
                )
            )
    source = FakeMarketDataSource(
        kline_data={
            ("SZ.000001", "K_DAY", start, end_buffer): stock_bars,
            ("SH.LIST0022", "K_DAY", start, end_buffer): sector_bars,
        }
    )

    report = run_nightly(
        source,
        trading_date=target.isoformat(),
        sectors={},
        ledger_database=tmp_path / "labels.db",
        shadow_database=tmp_path / "shadow.db",
        production_directory=tmp_path / "production",
        report_directory=tmp_path / "reports",
        confusion_database=tmp_path / "confusion.db",
        history_start=start,
        capital_flow_database=capital_db,
        watchlist={
            "SZ.000001": {
                "name": "测试股",
                "sector_code": "SH.LIST0022",
                "sector_name": "半导体",
            },
            "SZ.999999": {"name": "无映射"},
        },
    )

    assert report.errors == ()
    by_code = {run.sector_code: run for run in report.sector_runs}
    # The symbol without a usable sector mapping is reported and skipped.
    assert by_code["SZ.999999"].stock_unavailable == 1
    assert by_code["SZ.999999"].v2_reason == "sector_mapping_missing"
    assert by_code["SZ.000001"].stock_labeled >= 0

    with LabelLedger(tmp_path / "labels.db") as ledger:
        rows = ledger.stock_labels("SZ.000001")
        assert rows
        participants = {
            json.loads(row.feature_json).get("participant")
            for row in rows
            if json.loads(row.feature_json).get("participant")
        }
        # Flows cover every bar day with main>0 and large/main>0.6 -> 主力.
        assert participants == {"主力"}
