from __future__ import annotations

from src.labeler.sector_labeler_v2 import (
    ConstituentDailyObservation,
    SectorLabelerV2,
    TrendState,
)
from src.data.fake_client import FakeMarketDataSource
from src.data.models import LimitPoolRecord


def _rows():
    return (
        ConstituentDailyObservation("SH.600001", 3, True, False, 200.0, 100.0),
        ConstituentDailyObservation("SZ.000002", 3, True, False, 150.0, 100.0),
        ConstituentDailyObservation("SZ.000003", 0, False, True, 80.0, 100.0),
        ConstituentDailyObservation("SZ.000004", 0, False, False, 120.0, 100.0),
    )


def test_v2_uses_constituent_codes_and_persists_required_raw_metrics() -> None:
    result = SectorLabelerV2().label_day(
        sector_code="SH.BK0001",
        trading_date="2026-08-11",
        futu_constituent_codes=("SH.600001", "SZ.000002", "SZ.000003", "SZ.000004"),
        akshare_observations=_rows(),
        trend_state=TrendState.HIGH_ACCELERATING,
    )

    assert result.status == "labeled"
    assert result.metrics.highest_limit_streak == 3
    assert result.metrics.highest_streak_stock_count == 2
    assert result.metrics.highest_streak_constituent_ratio == 0.5
    assert result.metrics.rise_limit_count == 2
    assert result.metrics.fall_limit_count == 1
    assert result.metrics.limit_balance == 1 / 3
    assert result.metrics.limit_activity == 0.75
    assert result.cycle_position == "高潮"
    assert result.consensus_state in {"一致", "分歧"}
    assert result.consensus_direction in {"转强", "转弱", "未确认"}


def test_zero_denominator_returns_data_insufficient() -> None:
    result = SectorLabelerV2().label_day(
        sector_code="SH.BK0001",
        trading_date="2026-08-11",
        futu_constituent_codes=(),
        akshare_observations=(),
        trend_state=TrendState.UP_CONFIRMED,
    )

    assert result.status == "data_insufficient"
    assert result.cycle_position is None
    assert result.metrics.limit_balance is None
    assert result.metrics.limit_activity is None


def test_akshare_industry_string_does_not_assign_sector_membership() -> None:
    observations = _rows() + (
        ConstituentDailyObservation("SH.699999", 9, True, False, 500.0, 100.0),
    )
    result = SectorLabelerV2().label_day(
        sector_code="SH.BK0001",
        trading_date="2026-08-11",
        futu_constituent_codes=("SH.600001", "SZ.000002", "SZ.000003", "SZ.000004"),
        akshare_observations=observations,
        trend_state=TrendState.HIGH_ACCELERATING,
    )

    assert result.metrics.highest_limit_streak == 3


def test_unified_data_seam_keeps_futu_membership_separate_from_limit_pool() -> None:
    source = FakeMarketDataSource(
        sector_constituents={"SH.BK0001": ("SH.600001", "SZ.000002")},
        limit_pool_data={
            "2026-08-11": (
                LimitPoolRecord("2026-08-11", "600001", 2, "rise"),
                LimitPoolRecord("2026-08-11", "699999", 9, "rise"),
            )
        },
    )

    assert source.get_sector_constituents("SH.BK0001") == (
        "SH.600001", "SZ.000002"
    )
    assert source.get_limit_pool("2026-08-11")[1].code == "699999"


def test_one_extreme_stock_cannot_independently_make_a_sector_climax() -> None:
    observations = (
        ConstituentDailyObservation("SH.600001", 9, True, False, 500.0, 100.0),
        ConstituentDailyObservation("SZ.000002", 0, False, False, 120.0, 100.0),
        ConstituentDailyObservation("SZ.000003", 0, False, False, 120.0, 100.0),
        ConstituentDailyObservation("SZ.000004", 0, False, False, 120.0, 100.0),
    )

    result = SectorLabelerV2().label_day(
        sector_code="SH.BK0001",
        trading_date="2026-08-11",
        futu_constituent_codes=tuple(item.code for item in observations),
        akshare_observations=observations,
        trend_state=TrendState.HIGH_ACCELERATING,
    )

    assert result.metrics.highest_limit_streak == 9
    assert result.metrics.highest_streak_stock_count == 1
    assert result.cycle_position == "发酵"


def test_high_reversal_without_spreading_weakness_remains_fermentation() -> None:
    result = SectorLabelerV2().label_day(
        sector_code="SH.BK0001",
        trading_date="2026-08-11",
        futu_constituent_codes=tuple(item.code for item in _rows()),
        akshare_observations=_rows(),
        trend_state=TrendState.HIGH_REVERSING,
    )

    assert result.consensus_direction == "转强"
    assert result.cycle_position == "发酵"
    assert result.cycle_event == "平台整理"
