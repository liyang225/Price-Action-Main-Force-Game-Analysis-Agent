from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pytest

from src.data.fake_client import FakeMarketDataSource
from src.data.models import CapitalFlow, MarketSnapshot


WINDOW = 40  # the fixed capital-flow collection window (40 trading days)


def trading_dates(count: int, *, start: date = date(2026, 1, 1)) -> list[str]:
    current = start
    dates: list[str] = []
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


@dataclass
class CapitalFlowSpy:
    source: FakeMarketDataSource
    calls: list[tuple[str, str]] = field(default_factory=list)

    def get_capital_flow(self, code: str, day: str) -> CapitalFlow | None:
        self.calls.append((code, day))
        return self.source.get_capital_flow(code, day)


def flow(code: str, day: str) -> CapitalFlow:
    return CapitalFlow(day, code, 10.0, 5.0, 2.0, -3.0, 17.0)


@pytest.mark.parametrize("window_size", [40])
def test_collects_the_requested_window_and_scope_only(tmp_path, window_size: int) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger

    days = trading_dates(window_size)
    codes = ("SZ.000001", "SZ.000002", "BK.100001")
    source = CapitalFlowSpy(
        FakeMarketDataSource(
            capital_flow_data={(code, day): flow(code, day) for code in codes for day in days}
        )
    )
    ledger = CapitalFlowLedger(tmp_path / "capital-flow.db")
    collector = CapitalFlowCollector(source, ledger)

    report = collector.collect(
        trading_dates=days,
        watchlist_codes=codes[:2],
        sector_codes=codes[2:],
    )

    assert report.inserted_count == window_size * len(codes)
    assert report.skipped_count == 0
    assert not report.failures
    assert source.calls == [(code, day) for code in codes for day in days]
    assert ledger.count() == window_size * len(codes)


def test_repeated_runs_do_not_requery_or_duplicate_existing_entries(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger

    days = trading_dates(WINDOW)
    source = CapitalFlowSpy(
        FakeMarketDataSource(
            capital_flow_data={
                ("SZ.000001", day): flow("SZ.000001", day) for day in days
            }
        )
    )
    database = tmp_path / "capital-flow.db"
    first_ledger = CapitalFlowLedger(database)
    first = CapitalFlowCollector(source, first_ledger).collect(
        trading_dates=days, watchlist_codes=["SZ.000001"]
    )
    first_ledger.close()

    second_ledger = CapitalFlowLedger(database)
    second = CapitalFlowCollector(source, second_ledger).collect(
        trading_dates=days, watchlist_codes=["SZ.000001"]
    )

    assert first.inserted_count == WINDOW
    assert second.inserted_count == 0
    assert second.skipped_count == WINDOW
    assert len(source.calls) == WINDOW
    assert second_ledger.count() == WINDOW


def test_null_data_is_rejected_and_persisted_as_a_traceable_failure(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger

    days = trading_dates(WINDOW)
    source = CapitalFlowSpy(FakeMarketDataSource())
    ledger = CapitalFlowLedger(tmp_path / "capital-flow.db")

    report = CapitalFlowCollector(source, ledger).collect(
        trading_dates=days, watchlist_codes=["SZ.000001"]
    )

    assert report.inserted_count == 0
    assert report.failures[0].reason == "capital flow is unavailable"
    assert len(report.failures) == WINDOW
    assert ledger.count() == 0
    assert len(ledger.failures()) == WINDOW

    CapitalFlowCollector(source, ledger).collect(
        trading_dates=days, watchlist_codes=["SZ.000001"]
    )
    assert len(ledger.failures()) == WINDOW


def test_null_capital_flow_fields_are_rejected_before_writing(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger

    days = trading_dates(WINDOW)
    source = CapitalFlowSpy(
        FakeMarketDataSource(
            capital_flow_data={
                ("SZ.000001", day): CapitalFlow(day, "SZ.000001", 10, 5, 2, -3, None)
                for day in days
            }
        )
    )
    ledger = CapitalFlowLedger(tmp_path / "capital-flow.db")

    report = CapitalFlowCollector(source, ledger).collect(
        trading_dates=days, watchlist_codes=["SZ.000001"]
    )

    assert report.inserted_count == 0
    assert "main_in_flow must be a finite number" in report.failures[0].reason
    assert ledger.count() == 0


def test_a_partial_failure_is_recorded_and_is_collected_on_a_later_run(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger

    days = trading_dates(WINDOW)
    failed_target = ("SZ.000001", days[7])
    source = CapitalFlowSpy(
        FakeMarketDataSource(
            capital_flow_data={
                ("SZ.000001", day): flow("SZ.000001", day) for day in days
            },
            failures={
                ("get_capital_flow", failed_target): "OpenD temporarily unavailable"
            },
        )
    )
    ledger = CapitalFlowLedger(tmp_path / "capital-flow.db")
    collector = CapitalFlowCollector(source, ledger)

    first = collector.collect(trading_dates=days, watchlist_codes=["SZ.000001"])
    recovery_source = CapitalFlowSpy(
        FakeMarketDataSource(
            capital_flow_data={
                ("SZ.000001", day): flow("SZ.000001", day) for day in days
            }
        )
    )
    second = CapitalFlowCollector(recovery_source, ledger).collect(
        trading_dates=days, watchlist_codes=["SZ.000001"]
    )

    assert first.inserted_count == WINDOW - 1
    assert first.failures == (first.failures[0],)
    assert first.failures[0].code == "SZ.000001"
    assert first.failures[0].date == days[7]
    assert "temporarily unavailable" in first.failures[0].reason
    assert second.inserted_count == 1
    assert second.skipped_count == WINDOW - 1
    assert recovery_source.calls == [failed_target]
    assert ledger.count() == WINDOW
    # Once the recovered day is in the flows table, its failure record is gone.
    assert ledger.failures() == ()


def test_later_collection_window_replaces_expired_ledger_entries(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger

    first_window = trading_dates(WINDOW)
    second_window = trading_dates(WINDOW, start=date(2027, 1, 1))
    all_days = (*first_window, *second_window)
    source = CapitalFlowSpy(
        FakeMarketDataSource(
            capital_flow_data={
                ("SZ.000001", day): flow("SZ.000001", day) for day in all_days
            }
        )
    )
    ledger = CapitalFlowLedger(tmp_path / "capital-flow.db")
    collector = CapitalFlowCollector(source, ledger)

    collector.collect(trading_dates=first_window, watchlist_codes=["SZ.000001"])
    report = collector.collect(trading_dates=second_window, watchlist_codes=["SZ.000001"])

    assert report.inserted_count == WINDOW
    assert ledger.count() == WINDOW


def test_flows_for_returns_ledger_rows_oldest_first(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowLedger

    days = trading_dates(WINDOW)
    ledger = CapitalFlowLedger(tmp_path / "capital-flow.db")
    for index, day in enumerate(days):
        ledger.append(CapitalFlow(day, "SZ.000001", 10.0, 5.0, 2.0, -3.0, float(index)))

    flows = ledger.flows_for("SZ.000001")
    assert len(flows) == WINDOW
    assert [item.date for item in flows] == days
    assert flows[0].main_in_flow == 0.0
    assert flows[-1].main_in_flow == float(WINDOW - 1)
    assert ledger.flows_for("SZ.999999") == ()


@pytest.mark.parametrize("window_size", [39, 41])
def test_collection_window_is_fixed_at_40_trading_days(
    tmp_path, window_size: int
) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger

    with pytest.raises(ValueError, match="exactly 40"):
        CapitalFlowCollector(
            CapitalFlowSpy(FakeMarketDataSource()),
            CapitalFlowLedger(tmp_path / "capital-flow.db"),
        ).collect(trading_dates=trading_dates(window_size), watchlist_codes=["SZ.000001"])


def test_collection_scope_never_accepts_more_than_twenty_codes_per_group(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger

    with pytest.raises(ValueError, match="at most 20"):
        CapitalFlowCollector(
            CapitalFlowSpy(FakeMarketDataSource()),
            CapitalFlowLedger(tmp_path / "capital-flow.db"),
        ).collect(
            trading_dates=trading_dates(WINDOW),
            watchlist_codes=[f"SZ.{number:06d}" for number in range(21)],
        )


def test_range_fetch_uses_one_call_per_code(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger

    days = trading_dates(WINDOW)
    source = CapitalFlowSpy(
        FakeMarketDataSource(
            capital_flow_data={
                ("SZ.000001", day): flow("SZ.000001", day) for day in days
            }
        )
    )
    # The spy does not expose the range endpoint, so the collector must
    # fall back to per-day queries for it; the raw fake would use the range.
    ledger = CapitalFlowLedger(tmp_path / "capital-flow.db")
    report = CapitalFlowCollector(source, ledger).collect(
        trading_dates=days, watchlist_codes=["SZ.000001"]
    )
    assert report.inserted_count == WINDOW
    assert len(source.calls) == WINDOW


def test_range_fetch_failure_falls_back_to_per_day_queries(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger

    days = trading_dates(WINDOW)
    source = FakeMarketDataSource(
        capital_flow_data={
            ("SZ.000001", day): flow("SZ.000001", day) for day in days
        },
        failures={
            ("get_capital_flow_range", ("SZ.000001", days[0], days[-1])): "OpenD down"
        },
    )
    ledger = CapitalFlowLedger(tmp_path / "capital-flow.db")
    report = CapitalFlowCollector(source, ledger).collect(
        trading_dates=days, watchlist_codes=["SZ.000001"]
    )
    assert report.inserted_count == WINDOW
    assert len(report.failures) == 1
    assert "range fetch failed" in report.failures[0].reason


def test_sector_collection_uses_its_associated_name_and_keeps_sector_code(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger

    days = trading_dates(WINDOW)

    class Source(FakeMarketDataSource):
        def __init__(self) -> None:
            super().__init__()
            self.names: list[str] = []

        def get_sector_capital_flow_history(self, name: str):
            self.names.append(name)
            return tuple(flow(name, day) for day in days)

    source = Source()
    ledger = CapitalFlowLedger(tmp_path / "capital-flow.db")
    report = CapitalFlowCollector(source, ledger).collect(
        trading_dates=days,
        watchlist_codes=(),
        sector_codes=("SH.LIST0022",),
        sector_names={"SH.LIST0022": "半导体"},
    )

    assert report.inserted_count == WINDOW
    assert report.failures == ()
    assert source.names == ["半导体"]
    assert all(item.code == "SH.LIST0022" for item in ledger.flows_for("SH.LIST0022"))


def test_representative_sector_flow_uses_3_rise_2_fall_and_the_focus_stock(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger

    days = trading_dates(WINDOW)
    sector = "SH.LIST0022"
    components = ("SZ.000001", "SZ.000002", "SZ.000003", "SZ.000004", "SZ.000005")
    focus = "SZ.000006"
    rates = {
        "SZ.000001": 8.0,
        "SZ.000002": 6.0,
        "SZ.000003": 4.0,
        "SZ.000004": -5.0,
        "SZ.000005": -3.0,
        focus: 1.0,
    }
    source = FakeMarketDataSource(
        sector_constituents={sector: components},
        market_snapshots=rates,
        capital_flow_data={
            (code, days[-1]): CapitalFlow(days[-1], code, 1, 2, 3, 4, rate)
            for code, rate in rates.items()
        },
    )
    ledger = CapitalFlowLedger(tmp_path / "capital-flow.db")

    report = CapitalFlowCollector(source, ledger).collect(
        trading_dates=days,
        watchlist_codes=(),
        sector_codes=(sector,),
        sector_focus_codes={sector: (focus,)},
    )

    assert report.inserted_count == 1
    assert report.failures == ()
    assert [item.code for item in ledger.representative_basket_for(sector, days[-1])] == [
        "SZ.000001",
        "SZ.000002",
        "SZ.000003",
        "SZ.000004",
        "SZ.000005",
        focus,
    ]
    assert [item.date for item in ledger.flows_for(sector)] == [days[-1]]
    assert ledger.flows_for(sector)[0].main_in_flow == sum(rates.values())


def test_representative_sector_selection_is_frozen_and_never_backfills_old_days(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger

    days = trading_dates(WINDOW)
    sector = "SH.LIST0022"

    class Source(FakeMarketDataSource):
        def __init__(self) -> None:
            super().__init__(
                sector_constituents={sector: ("SZ.000001", "SZ.000002", "SZ.000003")},
                market_snapshots={"SZ.000001": 3.0, "SZ.000002": 2.0, "SZ.000003": -4.0},
                capital_flow_data={
                    (code, days[-1]): flow(code, days[-1])
                    for code in ("SZ.000001", "SZ.000002", "SZ.000003")
                },
            )
            self.snapshot_calls = 0

        def get_market_snapshots(self, codes):
            self.snapshot_calls += 1
            return super().get_market_snapshots(codes)

    source = Source()
    ledger = CapitalFlowLedger(tmp_path / "capital-flow.db")
    collector = CapitalFlowCollector(source, ledger)

    first = collector.collect(trading_dates=days, watchlist_codes=(), sector_codes=(sector,))
    second = collector.collect(trading_dates=days, watchlist_codes=(), sector_codes=(sector,))

    assert first.inserted_count == 1
    assert second.inserted_count == 0
    assert source.snapshot_calls == 1
    assert [item.date for item in ledger.flows_for(sector)] == [days[-1]]


def test_representative_member_selection_covers_both_price_directions_and_deduplicates_focus() -> None:
    from src.data.capital_flow_ledger import select_representative_members

    selected = select_representative_members(
        (
            MarketSnapshot("SZ.000001", 8),
            MarketSnapshot("SZ.000002", 6),
            MarketSnapshot("SZ.000003", 4),
            MarketSnapshot("SZ.000004", -3),
            MarketSnapshot("SZ.000005", -5),
        ),
        focus_codes=("SZ.000002", "SZ.000006"),
    )

    assert [item.code for item in selected] == [
        "SZ.000001",
        "SZ.000002",
        "SZ.000003",
        "SZ.000005",
        "SZ.000004",
    ]


def test_collection_blocks_until_a_rate_limit_slot_is_free(tmp_path) -> None:
    from src.data.capital_flow_ledger import CapitalFlowCollector, CapitalFlowLedger
    from src.data.rate_limiter import FakeClock, RateLimiter

    days = trading_dates(WINDOW)
    source = CapitalFlowSpy(
        FakeMarketDataSource(
            capital_flow_data={
                ("SZ.000001", day): flow("SZ.000001", day) for day in days
            }
        )
    )
    clock = FakeClock()
    limiter = RateLimiter(max_calls=2, window_seconds=30.0, clock=clock)
    ledger = CapitalFlowLedger(tmp_path / "capital-flow.db")
    collector = CapitalFlowCollector(
        source, ledger, rate_limiter=limiter, sleep=clock.advance
    )

    report = collector.collect(trading_dates=days, watchlist_codes=["SZ.000001"])

    # 40 per-day calls at 2 per 30s → the injected clock must have advanced.
    assert report.inserted_count == WINDOW
    assert report.failures == ()
    assert clock.now() >= 30.0 * ((WINDOW - 1) // 2)
