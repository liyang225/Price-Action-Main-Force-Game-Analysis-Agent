from __future__ import annotations

import gzip
import json

import pandas as pd
import pytest

from behavior_study.collector import (
    ApiError,
    RateLimiter,
    fetch_history_paginated,
    rank_current_eligible,
    select_configured_plates,
    write_collection,
)


def test_plate_selection_matches_configured_codes_and_names() -> None:
    plates = pd.DataFrame(
        {
            "code": ["SH.LIST0002", "SH.LIST0931", "SH.LIST9999"],
            "plate_name": ["半导体", "白酒Ⅱ", "其他"],
        }
    )
    configured = [
        {"code": "SH.LIST0002", "name": "半导体"},
        {"code": "SH.LIST0931", "name": "白酒Ⅱ"},
    ]

    selected = select_configured_plates(plates, configured)

    assert selected["code"].tolist() == ["SH.LIST0002", "SH.LIST0931"]


def test_rank_current_eligible_filters_status_st_and_listing_date() -> None:
    snapshot = pd.DataFrame(
        {
            "code": ["SH.600001", "SH.688001", "SZ.000001", "SZ.300001", "SZ.000002"],
            "name": ["甲", "乙", "ST丙", "丁", "戊"],
            "circular_market_val": [500, 900, 800, 700, 600],
            "listing_date": ["2020-01-01", "2020-01-01", "2020-01-01", "2023-01-01", "2020-01-01"],
            "listing_status": ["NORMAL", "NORMAL", "NORMAL", "NORMAL", "NORMAL"],
        }
    )

    selected = rank_current_eligible(snapshot, top_n=10, listed_by="2022-01-01")

    assert selected["code"].tolist() == ["SH.688001", "SZ.000002", "SH.600001"]


class _FakeQuote:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def request_history_kline(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["page_req_key"] is None:
            return 0, pd.DataFrame({"time_key": ["2024-01-01"], "close": [1.0]}), "next"
        return 0, pd.DataFrame({"time_key": ["2024-01-02"], "close": [1.1]}), None


def test_history_pagination_only_rate_limits_first_page() -> None:
    quote = _FakeQuote()
    sleeps: list[float] = []
    limiter = RateLimiter(min_interval_seconds=1.0, clock=lambda: 0.0, sleep=sleeps.append)

    history, metadata = fetch_history_paginated(
        quote,
        "SH.600001",
        start="2024-01-01",
        end="2024-01-03",
        rate_limiter=limiter,
    )

    assert len(history) == 2
    assert len(quote.calls) == 2
    assert metadata["page_count"] == 2
    assert len(sleeps) == 0
    assert quote.calls[1]["page_req_key"] == "next"


def test_history_pagination_rejects_repeated_page_key() -> None:
    class RepeatingQuote:
        def __init__(self) -> None:
            self.calls = 0

        def request_history_kline(self, **kwargs):
            self.calls += 1
            return 0, pd.DataFrame({"time_key": ["2024-01-01"]}), "same"

    quote = RepeatingQuote()

    with pytest.raises(ApiError, match="repeated page_req_key"):
        fetch_history_paginated(
            quote,
            "SH.600001",
            start="2024-01-01",
            end="2024-01-03",
            rate_limiter=RateLimiter(min_interval_seconds=0),
        )

    assert quote.calls == 2


def test_rate_limiter_rejects_non_finite_interval() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        RateLimiter(min_interval_seconds=float("nan"))


def test_rank_current_eligible_requires_listing_status() -> None:
    snapshot = pd.DataFrame(
        {
            "code": ["SH.600001"],
            "name": ["甲"],
            "circular_market_val": [500],
            "listing_date": ["2020-01-01"],
        }
    )

    with pytest.raises(ValueError, match="listing status"):
        rank_current_eligible(snapshot, top_n=1)


def test_write_collection_emits_gzip_csv_and_manifest(tmp_path) -> None:
    data = pd.DataFrame({"code": ["SH.600001"], "time_key": ["2024-01-01"], "close": [1.0]})
    manifest = write_collection(data, tmp_path, config={"start_date": "2024-01-01"})

    csv_path = tmp_path / "daily_ohlcv.csv.gz"
    assert csv_path.exists()
    with gzip.open(csv_path, "rt", encoding="utf-8") as handle:
        assert "SH.600001" in handle.read()
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["row_count"] == 1
    assert manifest["sha256"]
