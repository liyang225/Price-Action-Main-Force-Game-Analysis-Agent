"""Tests for the board-analysis data service (资金流向 / 连板信息 / 龙虎榜)."""

from __future__ import annotations

from datetime import date

from src.data.capital_flow_ledger import CapitalFlow, CapitalFlowLedger
from src.data.fake_client import FakeMarketDataSource
from src.data.models import DragonTiger, LimitPoolRecord
from src.integration.sector_analysis_service import (
    SectorAnalysisBundle,
    SectorAnalysisService,
)


def _ledger_with_flows(tmp_path, sector_code: str = "BK0475") -> str:
    database = tmp_path / "flows.db"
    with CapitalFlowLedger(database) as ledger:
        for day, main in (("2026-08-12", 120.0), ("2026-08-13", -45.0), ("2026-08-14", 88.0)):
            ledger.append(
                CapitalFlow(
                    date=day,
                    code=sector_code,
                    super_in_flow=main * 0.5,
                    big_in_flow=main * 0.3,
                    mid_in_flow=main * 0.1,
                    sml_in_flow=-main * 0.1,
                    main_in_flow=main,
                )
            )
    return str(database)


def _dragon(code: str, day: str = "2026-08-14") -> DragonTiger:
    return DragonTiger(
        date=day,
        code=code,
        reason="日涨幅偏离值达7%",
        net_buy_amount=500.0,
        buy_amount=1200.0,
        sell_amount=700.0,
        institution_net_buy=300.0,
        institution_net_sell=0.0,
        hot_money_net_buy=200.0,
        hot_money_net_sell=0.0,
        institution_seats=("机构专用",),
        hot_money_seats=("华泰证券总部",),
    )


def test_collect_aggregates_all_three_dimensions(tmp_path) -> None:
    source = FakeMarketDataSource(
        sector_constituents={"BK0475": ["SH.600000", "SZ.000001"]},
        limit_pool_data={
            "2026-08-14": [
                LimitPoolRecord("2026-08-14", "600000", 2, "rise"),
                LimitPoolRecord("2026-08-14", "603999", 5, "rise"),  # 非成分股，应被过滤
            ]
        },
        dragon_tiger_data={
            ("600000", "2026-08-14"): _dragon("600000"),
            ("000002", "2026-08-14"): _dragon("000002"),  # 非成分股
        },
    )
    service = SectorAnalysisService(
        source, capital_flow_database=_ledger_with_flows(tmp_path)
    )
    bundle = service.collect(sector_code="BK0475", sector_name="半导体", date="2026-08-14")

    assert bundle.status == "ready"
    assert bundle.sector_name == "半导体"
    # 资金流向：最近窗口内 3 天
    assert [item.date for item in bundle.capital_flow] == [
        "2026-08-12", "2026-08-13", "2026-08-14"
    ]
    assert bundle.capital_flow[-1].main_in_flow == 88.0
    # 连板：只有成分股 600000 命中，603999 被过滤；code 保留 AkShare 裸代码
    assert [(item.code, item.limit_streak) for item in bundle.limit_pool] == [
        ("600000", 2)
    ]
    # 龙虎榜：只有成分股 600000 命中并增强
    assert [item.code for item in bundle.dragon_tiger] == ["600000"]
    assert bundle.dragon_tiger[0].institution_net_buy == 300.0
    assert bundle.dragon_tiger[0].hot_money_seats == ("华泰证券总部",)
    assert bundle.errors == ()


def test_date_retro_falls_back_to_previous_trading_day(tmp_path) -> None:
    # 当日(14日)涨停池与龙虎榜均无数据，回退到 13 日命中
    source = FakeMarketDataSource(
        sector_constituents={"BK0475": ["SH.600000"]},
        limit_pool_data={
            "2026-08-13": [LimitPoolRecord("2026-08-13", "600000", 3, "rise")]
        },
        dragon_tiger_data={("600000", "2026-08-13"): _dragon("600000", "2026-08-13")},
    )
    service = SectorAnalysisService(source)
    bundle = service.collect(sector_code="BK0475", date="2026-08-14")

    assert bundle.status == "ready"
    assert bundle.limit_pool[0].limit_streak == 3
    assert bundle.limit_pool[0].date == "2026-08-13"
    assert bundle.dragon_tiger[0].date == "2026-08-13"
    assert bundle.dragon_tiger[0].net_buy_amount == 500.0


def test_bare_and_prefixed_codes_match_across_sources(tmp_path) -> None:
    source = FakeMarketDataSource(
        sector_constituents={"BK0475": ["600000"]},  # AkShare 风格裸代码成分股
        limit_pool_data={
            "2026-08-14": [LimitPoolRecord("2026-08-14", "SH.600000", 1, "rise")]
        },
    )
    service = SectorAnalysisService(source)
    bundle = service.collect(sector_code="BK0475", date="2026-08-14")
    assert bundle.limit_pool[0].code == "SH.600000"


def test_constituents_failure_degrades_without_raising(tmp_path) -> None:
    from src.data.protocol import DataSourceError

    source = FakeMarketDataSource(
        failures={"get_sector_constituents": DataSourceError("富途未连接")},
        capital_flow_data={},
    )
    service = SectorAnalysisService(
        source, capital_flow_database=_ledger_with_flows(tmp_path)
    )
    bundle = service.collect(sector_code="BK0475", date="2026-08-14")

    # 资金流是本地台账，不受影响；连板/龙虎榜因成分股失败而缺失
    assert len(bundle.capital_flow) == 3
    assert bundle.limit_pool == ()
    assert bundle.dragon_tiger == ()
    assert bundle.status == "partial"
    assert any("成分股" in error for error in bundle.errors)


def test_all_dimensions_empty_yields_unavailable(tmp_path) -> None:
    source = FakeMarketDataSource()  # 无成分股、无涨停池、无龙虎榜
    service = SectorAnalysisService(source)
    bundle = service.collect(sector_code="BK0475", date="2026-08-14")
    assert bundle.status == "unavailable"
    assert bundle.capital_flow == ()
    assert bundle.limit_pool == ()
    assert bundle.dragon_tiger == ()


def test_empty_sector_code_returns_unavailable(tmp_path) -> None:
    service = SectorAnalysisService(FakeMarketDataSource())
    bundle = service.collect(sector_code="  ", date="2026-08-14")
    assert bundle.status == "unavailable"
    assert bundle.errors


def test_bundle_to_dict_round_trip(tmp_path) -> None:
    source = FakeMarketDataSource(
        sector_constituents={"BK0475": ["SH.600000"]},
        limit_pool_data={
            "2026-08-14": [LimitPoolRecord("2026-08-14", "600000", 2, "rise")]
        },
        dragon_tiger_data={("600000", "2026-08-14"): _dragon("600000")},
    )
    service = SectorAnalysisService(source)
    payload = service.collect(sector_code="BK0475", sector_name="半导体", date="2026-08-14").to_dict()

    assert payload["status"] == "ready"
    assert payload["sector_name"] == "半导体"
    assert payload["limit_pool"][0]["limit_streak"] == 2
    assert payload["limit_pool"][0]["direction"] == "rise"
    assert payload["dragon_tiger"][0]["institution_net_buy"] == 300.0
    assert isinstance(payload["raw"], dict)
