"""Live AkShare smoke tests (network required). Baostock fallback disabled."""
from __future__ import annotations

import time

import pytest

from pa_agent.data.akshare_source import AkShareSource

pytestmark = [pytest.mark.live, pytest.mark.integration]


def _source() -> AkShareSource:
    s = AkShareSource()
    s.connect()
    assert s._baostock_ok is False, "tests must not use Baostock fallback"
    return s


@pytest.fixture(scope="module")
def akshare_available() -> None:
    pytest.importorskip("akshare")


def test_live_stock_1h(akshare_available: None) -> None:
    s = _source()
    s.subscribe("000001", "1h")
    bars = s.latest_snapshot(20)
    assert len(bars) >= 10
    assert bars[0].close > 0
    assert bars[0].high >= bars[0].low


def test_live_stock_1d(akshare_available: None) -> None:
    time.sleep(2)
    s = _source()
    s.subscribe("600519", "1d")
    bars = s.latest_snapshot(30)
    assert len(bars) >= 20
    assert all(b.high >= b.low for b in bars[:5])


def test_live_stock_4h(akshare_available: None) -> None:
    time.sleep(2)
    s = _source()
    s.subscribe("000001", "4h")
    bars = s.latest_snapshot(15)
    assert len(bars) >= 5


def test_live_three_snapshots_stable(akshare_available: None) -> None:
    """Three consecutive fetches (simulates RefreshLoop) without fallback."""
    time.sleep(2)
    s = _source()
    s.subscribe("000001", "1h")
    for _ in range(3):
        bars = s.latest_snapshot(10)
        assert len(bars) == 10
        time.sleep(1.2)
