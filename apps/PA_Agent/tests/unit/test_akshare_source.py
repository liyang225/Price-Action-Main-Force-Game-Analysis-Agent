"""Unit tests for AkShare data source helpers (no network)."""
from __future__ import annotations

from pa_agent.data.akshare_source import (
    _resample_rows_to_2h,
    _resample_rows_to_4h,
    is_index_symbol,
    normalize_ashare_symbol,
)


def test_normalize_ashare_symbol_stock():
    assert normalize_ashare_symbol("600519") == "600519"
    assert normalize_ashare_symbol("sh600519") == "600519"


def test_normalize_ashare_symbol_index():
    assert normalize_ashare_symbol("sh000300") == "sh000300"
    assert normalize_ashare_symbol("000300") == "000300"


def test_is_index_symbol():
    assert is_index_symbol("000300") is True
    assert is_index_symbol("600519") is False
    assert is_index_symbol("000001") is False


def test_resample_60m_to_4h():
    rows = [
        {"ts_open": i, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1.0}
        for i in range(8)
    ]
    out = _resample_rows_to_4h(rows)
    assert len(out) == 2
    assert out[0]["open"] == 10.0
    assert out[0]["close"] == rows[3]["close"]
    assert out[0]["volume"] == 4.0


def test_resample_60m_to_2h_never_combines_different_trading_days():
    rows = []
    for day in ("2026-08-11", "2026-08-12"):
        for index, clock in enumerate(("10:30", "11:30", "14:00", "15:00"), 1):
            rows.append(
                {
                    "ts_open": f"{day} {clock}:00",
                    "open": float(index),
                    "high": float(index + 1),
                    "low": float(index - 1),
                    "close": float(index) + 0.5,
                    "volume": 10.0,
                }
            )

    out = _resample_rows_to_2h(rows)

    assert len(out) == 4
    assert [row["ts_open"] for row in out] == [
        "2026-08-11 10:30:00",
        "2026-08-11 14:00:00",
        "2026-08-12 10:30:00",
        "2026-08-12 14:00:00",
    ]
    assert all(row["volume"] == 20.0 for row in out)
