from __future__ import annotations

from datetime import datetime

import pytest

from src.data.daily_cache import DailyMaterialCache
from src.data.fake_client import FakeMarketDataSource
from src.data.models import DragonTiger
from src.signals.dragon_tiger import (
    DRAGON_TIGER_CACHE_CATEGORY,
    DragonTigerSignalExtractor,
    SignalStatus,
)


def _record(**overrides) -> DragonTiger:
    values = {
        "date": "2026-08-11",
        "code": "SZ.000001",
        "reason": "涨幅偏离",
        "net_buy_amount": 1000.0,
        "institution_net_buy": 700.0,
        "hot_money_net_sell": 200.0,
        "institution_seats": ("机构专用",),
        "hot_money_seats": ("营业部A",),
        "source": "AkShare",
        "source_reference": "stock_lhb_detail_em:2026-08-11",
    }
    values.update(overrides)
    return DragonTiger(**values)


def test_signal_extractor_caches_auditable_institution_and_hot_money_features(tmp_path) -> None:
    record = _record()
    source = FakeMarketDataSource(dragon_tiger_data={(record.code, record.date): record})
    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 11, 11, 30)
    )

    result = DragonTigerSignalExtractor(source, cache).extract(record.code, record.date)

    assert result.status is SignalStatus.OK
    assert result.institution_net_buy == 700.0
    assert result.hot_money_net_sell == 200.0
    assert result.source_reference == record.source_reference
    assert cache.snapshot().materials[DRAGON_TIGER_CACHE_CATEGORY][record.code] == result


def test_signal_extractor_distinguishes_missing_data_from_zero_values(tmp_path) -> None:
    source = FakeMarketDataSource(dragon_tiger_data={("SZ.000001", "2026-08-11"): None})
    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 11, 11, 30)
    )

    result = DragonTigerSignalExtractor(source, cache).extract("SZ.000001", "2026-08-11")

    assert result.status is SignalStatus.NO_DATA
    assert result.institution_net_buy is None
    assert result.hot_money_net_sell is None


def test_signal_extractor_marks_conflicting_seat_directions_without_inventing_signals(tmp_path) -> None:
    record = _record(institution_seats=("机构专用",), hot_money_seats=("机构专用",))
    source = FakeMarketDataSource(dragon_tiger_data={(record.code, record.date): record})
    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 11, 11, 30)
    )

    result = DragonTigerSignalExtractor(source, cache).extract(record.code, record.date)

    assert result.status is SignalStatus.CONFLICT
    assert result.institution_net_buy is None
    assert result.hot_money_net_buy is None


def test_signal_extractor_combines_signed_sides_across_multiple_daily_records(tmp_path) -> None:
    buy = _record(institution_net_buy=700.0, institution_net_sell=None)
    sell = _record(
        institution_net_buy=None,
        institution_net_sell=250.0,
        hot_money_net_buy=100.0,
        hot_money_net_sell=None,
    )
    source = FakeMarketDataSource(
        dragon_tiger_data={(buy.code, buy.date): (buy, sell)}
    )
    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 11, 11, 30)
    )

    result = DragonTigerSignalExtractor(source, cache).extract(buy.code, buy.date)

    assert result.institution_net_buy == 700.0
    assert result.institution_net_sell == 250.0
    assert result.status is SignalStatus.OK


def test_malformed_offline_records_are_cached_as_an_explicit_source_error(tmp_path) -> None:
    source = FakeMarketDataSource(
        dragon_tiger_data={("SZ.000001", "2026-08-11"): ({"bad": "record"},)}
    )
    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 11, 11, 30)
    )

    result = DragonTigerSignalExtractor(source, cache).extract(
        "SZ.000001", "2026-08-11"
    )

    assert result.status is SignalStatus.SOURCE_ERROR
    assert cache.snapshot().materials[DRAGON_TIGER_CACHE_CATEGORY]["SZ.000001"] == result


def test_signal_extractor_rejects_impossible_calendar_dates(tmp_path) -> None:
    cache = DailyMaterialCache(
        tmp_path / "archives", clock=lambda: datetime(2026, 8, 11, 11, 30)
    )

    with pytest.raises(ValueError, match="valid calendar"):
        DragonTigerSignalExtractor(FakeMarketDataSource(), cache).extract(
            "SZ.000001", "2026-02-30"
        )
