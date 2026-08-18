"""Tests for timezone-safe market data timestamp helpers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pa_agent.data.datetime_ts import (
    datetime_to_ts_ms,
    format_epoch_for_display,
    naive_local_to_utc,
    ts_open_to_ms,
)


def test_naive_datetime_uses_utc_wall_clock_not_local_timestamp():
    """Naive API datetimes must not go through host-local ``timestamp()``."""
    dt = datetime(2024, 6, 15, 12, 30, 0)
    expected = 1_718_454_600_000
    assert datetime_to_ts_ms(dt) == expected


def test_aware_utc_datetime_converts_correctly():
    dt = datetime(2024, 6, 15, 12, 30, 0, tzinfo=timezone.utc)
    assert datetime_to_ts_ms(dt) == 1_718_454_600_000


def test_pandas_timestamp_naive_treated_as_utc():
    pd = pytest.importorskip("pandas")
    ts = pd.Timestamp("2024-06-15 12:30:00")
    assert datetime_to_ts_ms(ts) == 1_718_454_600_000


def test_pandas_timestamp_aware_converts_to_utc_ms():
    pd = pytest.importorskip("pandas")
    ts = pd.Timestamp("2024-06-15 08:30:00", tz="America/New_York")
    assert datetime_to_ts_ms(ts) == 1_718_454_600_000


def test_ts_open_to_ms_seconds_and_milliseconds() -> None:
    assert ts_open_to_ms(1_718_454_600) == 1_718_454_600_000.0
    assert ts_open_to_ms(1_718_454_600_000) == 1_718_454_600_000.0


def test_format_epoch_for_display_no_local_shift():
    assert format_epoch_for_display(1_718_454_600_000, short=True) == "2024-06-15 12:30"
    assert format_epoch_for_display(1_718_454_600, short=False) == "2024-06-15 12:30:00"


def test_naive_local_to_utc_uses_host_offset():
  # 2024-06-15 20:30 in UTC+8 == 2024-06-15 12:30 UTC
    import time as _time

    if _time.timezone == 0 and not _time.daylight:
        pytest.skip("host is UTC")

    local = datetime(2024, 6, 15, 20, 30, 0)
    utc = naive_local_to_utc(local)
    assert utc.tzinfo == timezone.utc
    assert utc.hour == 12
    assert utc.minute == 30
