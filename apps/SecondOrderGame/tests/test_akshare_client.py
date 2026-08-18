from __future__ import annotations

import time

import pytest

from src.data.akshare_client import AkShareApiError, AkShareMarketDataSource
from src.data.models import CapitalFlow, DragonTiger
from src.data.protocol import DataSourceError, MarketDataSource


class FakeAkShare:
    def stock_lhb_detail_em(self, *, start_date: str, end_date: str):
        assert start_date == end_date == "20260811"
        return [
            {
                "代码": "000001",
                "日期": "2026-08-11",
                "上榜原因": "日涨幅偏离值达7%",
                "龙虎榜净买额": "1,000.50",
                "龙虎榜买入额": 2000.5,
                "龙虎榜卖出额": 1000,
                "机构净买额": 700.5,
                "游资净买额": -200,
                "机构席位": "机构专用",
                "游资席位": "营业部A",
            }
        ]


def test_akshare_adapter_normalizes_detail_rows_at_the_market_data_seam() -> None:
    source = AkShareMarketDataSource(akshare_module=FakeAkShare())

    assert isinstance(source, MarketDataSource)
    result = source.get_dragon_tiger("SZ.000001", "2026-08-11")

    assert result == DragonTiger(
        date="2026-08-11",
        code="SZ.000001",
        reason="日涨幅偏离值达7%",
        net_buy_amount=1000.5,
        buy_amount=2000.5,
        sell_amount=1000.0,
        institution_net_buy=700.5,
        institution_net_sell=None,
        hot_money_net_buy=None,
        hot_money_net_sell=200.0,
        institution_seats=("机构专用",),
        hot_money_seats=("营业部A",),
        source="AkShare",
        source_reference="stock_lhb_detail_em:2026-08-11",
    )


def test_akshare_adapter_returns_none_for_a_valid_empty_result() -> None:
    class Empty:
        def stock_lhb_detail_em(self, **kwargs):
            return []

    assert AkShareMarketDataSource(akshare_module=Empty()).get_dragon_tiger(
        "SZ.000001", "2026-08-11"
    ) is None


def test_akshare_adapter_normalizes_sector_history_in_ten_thousand_yuan() -> None:
    class SectorHistory:
        def stock_sector_fund_flow_hist(self, *, symbol: str):
            assert symbol == "半导体"
            return [
                {
                    "日期": "2026-08-14",
                    "主力净流入-净额": 1_200_000,
                    "超大单净流入-净额": 700_000,
                    "大单净流入-净额": 300_000,
                    "中单净流入-净额": -100_000,
                    "小单净流入-净额": -200_000,
                }
            ]

    result = AkShareMarketDataSource(
        akshare_module=SectorHistory()
    ).get_sector_capital_flow_history("半导体")

    assert result == (
        CapitalFlow(
            date="2026-08-14",
            code="半导体",
            super_in_flow=70.0,
            big_in_flow=30.0,
            mid_in_flow=-10.0,
            sml_in_flow=-20.0,
            main_in_flow=120.0,
        ),
    )


def test_daily_list_treats_akshare_null_result_type_error_as_no_records() -> None:
    class NullResult:
        def stock_lhb_detail_em(self, **kwargs):
            raise TypeError("'NoneType' object is not subscriptable")

    source = AkShareMarketDataSource(akshare_module=NullResult())

    assert source.get_dragon_tiger_day("2026-08-17") == ()


def test_akshare_adapter_rejects_impossible_calendar_dates() -> None:
    with pytest.raises(ValueError, match="valid calendar"):
        AkShareMarketDataSource(akshare_module=FakeAkShare()).get_dragon_tiger(
            "000001", "2026-02-30"
        )


def test_akshare_adapter_converts_provider_errors_to_data_source_errors() -> None:
    class Broken:
        def stock_lhb_detail_em(self, **kwargs):
            raise RuntimeError("network down")

    with pytest.raises(DataSourceError, match="network down"):
        AkShareMarketDataSource(akshare_module=Broken()).get_dragon_tiger(
            "000001", "2026-08-11"
        )


def test_akshare_adapter_rejects_malformed_required_fields_instead_of_zero_filling() -> None:
    class Malformed:
        def stock_lhb_detail_em(self, **kwargs):
            return [{"代码": "000001", "日期": "2026-08-11", "龙虎榜净买额": "not-a-number"}]

    with pytest.raises(AkShareApiError, match="invalid"):
        AkShareMarketDataSource(akshare_module=Malformed()).get_dragon_tiger(
            "000001", "2026-08-11"
        )


def test_akshare_adapter_uses_optional_official_institution_and_seat_endpoints() -> None:
    class Enriched:
        def stock_lhb_detail_em(self, **kwargs):
            return [{"代码": "000001", "上榜日": "2026-08-11", "龙虎榜净买额": 100}]

        def stock_lhb_jgmmtj_em(self, **kwargs):
            return [{"代码": "000001", "上榜日期": "2026-08-11", "机构买入净额": -25}]

        def stock_lhb_stock_detail_em(self, **kwargs):
            if kwargs["flag"] == "买入":
                return [{"交易营业部名称": "营业部A", "净额": 80}]
            return [{"交易营业部名称": "机构专用", "净额": 25}]

    result = AkShareMarketDataSource(akshare_module=Enriched()).get_dragon_tiger(
        "SZ.000001", "2026-08-11"
    )

    assert result is not None
    assert result.institution_net_buy is None
    assert result.institution_net_sell == 25
    assert result.hot_money_net_buy == 80
    assert result.hot_money_seats == ("营业部A",)
    assert result.buy_seats == ("营业部A",)
    assert result.sell_seats == ("机构专用",)


def test_duplicate_listing_reasons_do_not_double_count_the_same_trade_totals() -> None:
    class DuplicateReasons:
        def stock_lhb_detail_em(self, **kwargs):
            common = {
                "代码": "000001",
                "上榜日": "2026-08-11",
                "龙虎榜净买额": 100,
                "龙虎榜买入额": 300,
                "龙虎榜卖出额": 200,
            }
            return [
                {**common, "上榜原因": "涨幅偏离"},
                {**common, "上榜原因": "换手率达标"},
            ]

    result = AkShareMarketDataSource(
        akshare_module=DuplicateReasons()
    ).get_dragon_tiger("SZ.000001", "2026-08-11")

    assert result is not None
    assert result.net_buy_amount == 100
    assert result.buy_amount == 300
    assert result.sell_amount == 200
    assert result.reason == "涨幅偏离；换手率达标"


def test_primary_rows_filter_northbound_seats_before_the_business_boundary() -> None:
    class Northbound:
        def stock_lhb_detail_em(self, **kwargs):
            return [
                {
                    "代码": "000001",
                    "上榜日": "2026-08-11",
                    "龙虎榜净买额": 100,
                    "机构席位": "深股通专用,机构专用",
                    "游资席位": "沪股通专用,营业部A",
                }
            ]

    result = AkShareMarketDataSource(akshare_module=Northbound()).get_dragon_tiger(
        "SZ.000001", "2026-08-11"
    )

    assert result is not None
    assert result.institution_seats == ("机构专用",)
    assert result.hot_money_seats == ("营业部A",)


def test_one_deadline_bounds_summary_and_optional_enrichment_calls() -> None:
    class SlowEnrichment:
        def stock_lhb_detail_em(self, **kwargs):
            return [{"代码": "000001", "上榜日": "2026-08-11", "龙虎榜净买额": 100}]

        def stock_lhb_jgmmtj_em(self, **kwargs):
            time.sleep(0.015)
            return []

        def stock_lhb_stock_detail_em(self, **kwargs):
            time.sleep(0.015)
            return []

    started = time.monotonic()
    with pytest.raises(AkShareApiError, match="exceeded"):
        AkShareMarketDataSource(
            akshare_module=SlowEnrichment(), timeout_seconds=0.02
        ).get_dragon_tiger("SZ.000001", "2026-08-11")

    assert time.monotonic() - started < 0.08
