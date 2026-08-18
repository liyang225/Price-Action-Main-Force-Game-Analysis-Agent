"""Tests for the daily capital-flow collection scheduler (ROADMAP P0-8)."""

from __future__ import annotations

from datetime import date, timedelta
import json

import pytest

from src.data.capital_flow_daily import (
    run_capital_flow_catchup,
    run_daily_capital_flow,
    trading_window,
)
from src.data.capital_flow_ledger import CapitalFlowLedger
from src.data.fake_client import FakeMarketDataSource
from src.data.models import CapitalFlow


def _flow(code: str, day: str) -> CapitalFlow:
    return CapitalFlow(day, code, 10.0, 5.0, 2.0, -3.0, 17.0)


def _trading_dates(count: int, *, start: date = date(2026, 5, 1)) -> list[date]:
    current = start
    result: list[date] = []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def _source(*codes: str) -> FakeMarketDataSource:
    # Cover the 40-day window ending 2026-07-31 (backed by a wider calendar).
    days = _trading_dates(90, start=date(2026, 4, 1))
    return FakeMarketDataSource(
        capital_flow_data={
            (code, day.isoformat()): _flow(code, day.isoformat())
            for code in codes
            for day in days
        }
    )


def test_trading_window_returns_the_last_days_from_the_calendar() -> None:
    source = _source("SZ.000001")
    window = trading_window(source, "2026-07-31", days=40)
    assert len(window) == 40
    assert window[-1] == "2026-07-31"
    assert window == tuple(sorted(window))


def test_daily_collection_inserts_exactly_the_window_for_the_scope(tmp_path) -> None:
    codes = ("SZ.000001", "SZ.000002", "BK.100001")
    source = _source(*codes)
    database = tmp_path / "capital-flow.db"

    report = run_daily_capital_flow(
        source,
        as_of="2026-07-31",
        watchlist_codes=codes[:2],
        sector_codes=codes[2:],
        ledger_database=database,
    )

    assert report.errors == ()
    assert len(report.window_days) == 40
    assert report.inserted_count == 40 * len(codes)
    assert report.skipped_count == 0
    assert report.failures == ()
    with CapitalFlowLedger(database) as ledger:
        assert ledger.count() == 40 * len(codes)


def test_daily_collection_is_idempotent_across_runs(tmp_path) -> None:
    source = _source("SZ.000001")
    database = tmp_path / "capital-flow.db"

    first = run_daily_capital_flow(
        source, as_of="2026-07-31", watchlist_codes=["SZ.000001"], ledger_database=database
    )
    second = run_daily_capital_flow(
        source, as_of="2026-07-31", watchlist_codes=["SZ.000001"], ledger_database=database
    )

    assert first.inserted_count == 40
    assert second.inserted_count == 0
    assert second.skipped_count == 40
    with CapitalFlowLedger(database) as ledger:
        assert ledger.count() == 40


def test_daily_collection_rejects_an_empty_scope(tmp_path) -> None:
    source = _source("SZ.000001")
    report = run_daily_capital_flow(
        source,
        as_of="2026-07-31",
        watchlist_codes=[],
        ledger_database=tmp_path / "capital-flow.db",
    )
    assert report.errors
    assert "empty" in report.errors[0]


def test_catchup_reports_caught_up_without_network_work(tmp_path) -> None:
    days = _trading_dates(40)
    source = FakeMarketDataSource(
        capital_flow_data={
            ("SZ.000001", day.isoformat()): _flow("SZ.000001", day.isoformat())
            for day in days
        }
    )
    database = tmp_path / "capital-flow.db"
    as_of = days[-1].isoformat()

    run_daily_capital_flow(
        source, as_of=as_of, watchlist_codes=["SZ.000001"], ledger_database=database
    )
    report = run_capital_flow_catchup(
        source, as_of=as_of, watchlist_codes=["SZ.000001"], ledger_database=database
    )

    assert report.caught_up is True
    assert report.inserted_count == 0
    assert report.errors == ()


def test_catchup_backfills_only_the_stale_scope_code(tmp_path) -> None:
    days = _trading_dates(40)
    source = FakeMarketDataSource(
        capital_flow_data={
            (code, day.isoformat()): _flow(code, day.isoformat())
            for code in ("SZ.000001", "SZ.000002")
            for day in days
        }
    )
    database = tmp_path / "capital-flow.db"
    as_of = days[-1].isoformat()

    run_daily_capital_flow(
        source, as_of=as_of, watchlist_codes=["SZ.000001"], ledger_database=database
    )
    report = run_capital_flow_catchup(
        source,
        as_of=as_of,
        watchlist_codes=["SZ.000001", "SZ.000002"],
        ledger_database=database,
    )

    assert report.caught_up is False
    assert report.inserted_count == 40  # only the newly added code
    with CapitalFlowLedger(database) as ledger:
        assert ledger.count() == 80


def test_scope_file_round_trips_and_recovers_from_missing(tmp_path) -> None:
    from src.data.capital_flow_daily import load_scope, save_scope

    path = tmp_path / "scope.json"
    assert load_scope(path) == ((), ())

    save_scope(["SZ.000001", "SZ.000002 "], ["BK0475"], path=path)
    watchlist, sectors = load_scope(path)
    assert watchlist == ("SZ.000001", "SZ.000002")
    assert sectors == ("BK0475",)


def test_pa_watchlist_codes_normalizes_exchange_and_prefix(tmp_path) -> None:
    from src.data.capital_flow_daily import pa_watchlist_codes

    path = tmp_path / "watchlist.json"
    path.write_text(
        '{"items": ['
        '{"symbol": "512800", "exchange": "SSE", "name": "银行ETF"},'
        '{"symbol": "159732", "exchange": "SZSE", "name": "消费ETF"},'
        '{"symbol": "600584", "exchange": "", "name": "长电科技"},'
        '{"symbol": "688825", "exchange": "", "name": "C长鑫"},'
        '{"symbol": "HK.00700", "exchange": "", "name": "腾讯"}'
        "]}",
        encoding="utf-8",
    )
    codes = pa_watchlist_codes(path)
    assert codes == (
        "SH.512800",
        "SZ.159732",
        "SH.600584",
        "SH.688825",
        "HK.00700",
    )


def test_load_pa_settings_mapping_filters_unusable_sector_codes(tmp_path) -> None:
    from src.data.capital_flow_daily import load_pa_settings_mapping

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "second_order": {
                    "symbol_preferences": {
                        "SZ.159732": {
                            "sector_name": "半导体",
                            "sector_code": "SH.LIST0022",
                        },
                        "159865": {"sector_name": "养殖", "sector_code": "SH.LIST0011"},
                        "SH.517400": {"sector_name": "", "sector_code": "12"},
                        "SH.515220": {"sector_name": "", "sector_code": ""},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    mapping = load_pa_settings_mapping(path)
    assert mapping == {
        "SZ.159732": {"sector_code": "SH.LIST0022", "sector_name": "半导体"},
        "SZ.159865": {"sector_code": "SH.LIST0011", "sector_name": "养殖"},
    }
    with pytest.raises(ValueError, match="无法读取 PA 设置"):
        load_pa_settings_mapping(tmp_path / "missing.json")
