"""Tests for PreflightDataGate (check_preflight_data).

Tests cover:
- Three categories of data insufficiency each triggering correct failed_check
- Sufficient valid frame → ok=True
- Boundary cases n=19 (fail) and n=20 (pass)
- Robustness (malformed input)
- Hypothesis property tests (Property 1)
"""
from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pa_agent.ai.decision_nodes import (
    BAR_COUNT_THRESHOLD,
    check_preflight_data,
)
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame


def _make_bar(seq: int, *, high: float = 2010.0, low: float = 1990.0) -> KlineBar:
    return KlineBar(
        seq=seq,
        ts_open=float(1_000_000 - seq * 60_000),
        open=2000.0,
        high=high,
        low=low,
        close=2005.0,
        volume=1.0,
        closed=True,
    )


def _make_frame(n: int = 20, *, all_nan_ema: bool = False, all_nan_atr: bool = False) -> KlineFrame:
    bars = tuple(_make_bar(i + 1) for i in range(n))
    ema = tuple([float("nan")] * n if all_nan_ema else [2000.0] * n)
    atr = tuple([float("nan")] * n if all_nan_atr else [10.0] * n)
    return KlineFrame(
        symbol="TEST",
        timeframe="1h",
        bars=bars,
        snapshot_ts_local_ms=1,
        indicators=IndicatorBundle(ema20=ema, atr14=atr),
    )


# ── Category 1: bars_empty_or_bad_ohlc ───────────────────────────────────────

def test_none_frame_returns_bars_empty():
    result = check_preflight_data(None)
    assert result.ok is False
    assert result.failed_check == "bars_empty_or_bad_ohlc"


def test_empty_bars_returns_bars_empty():
    frame = KlineFrame(
        symbol="TEST", timeframe="1h",
        bars=(),
        snapshot_ts_local_ms=1,
        indicators=IndicatorBundle(ema20=(), atr14=()),
    )
    result = check_preflight_data(frame)
    assert result.ok is False
    assert result.failed_check == "bars_empty_or_bad_ohlc"


def test_bar_with_high_less_than_low():
    bars = (
        KlineBar(seq=1, ts_open=1.0, open=100.0, high=90.0, low=110.0, close=100.0, volume=1.0, closed=True),
    ) + tuple(_make_bar(i + 2) for i in range(20))
    frame = KlineFrame(
        symbol="TEST", timeframe="1h",
        bars=bars,
        snapshot_ts_local_ms=1,
        indicators=IndicatorBundle(ema20=tuple([2000.0] * len(bars)), atr14=tuple([10.0] * len(bars))),
    )
    result = check_preflight_data(frame)
    assert result.ok is False
    assert result.failed_check == "bars_empty_or_bad_ohlc"


# ── Category 2: bar_count_lt_20 ───────────────────────────────────────────────

def test_19_bars_fails():
    result = check_preflight_data(_make_frame(19))
    assert result.ok is False
    assert result.failed_check == "bar_count_lt_20"


def test_20_bars_passes_count():
    result = check_preflight_data(_make_frame(20))
    assert result.ok is True
    assert result.failed_check is None


def test_boundary_exactly_threshold():
    """n=BAR_COUNT_THRESHOLD should pass."""
    result = check_preflight_data(_make_frame(BAR_COUNT_THRESHOLD))
    assert result.ok is True


def test_boundary_one_below_threshold():
    """n=BAR_COUNT_THRESHOLD-1 should fail."""
    result = check_preflight_data(_make_frame(BAR_COUNT_THRESHOLD - 1))
    assert result.ok is False
    assert result.failed_check == "bar_count_lt_20"


# ── Category 3: indicators_all_nan ────────────────────────────────────────────

def test_all_nan_indicators_fails():
    result = check_preflight_data(_make_frame(20, all_nan_ema=True, all_nan_atr=True))
    assert result.ok is False
    assert result.failed_check == "indicators_all_nan"


def test_only_ema_nan_passes_if_atr_valid():
    """If only EMA is NaN but ATR is valid, should pass."""
    result = check_preflight_data(_make_frame(20, all_nan_ema=True, all_nan_atr=False))
    assert result.ok is True


def test_only_atr_nan_passes_if_ema_valid():
    """If only ATR is NaN but EMA is valid, should pass."""
    result = check_preflight_data(_make_frame(20, all_nan_ema=False, all_nan_atr=True))
    assert result.ok is True


# ── Sufficient valid frame ─────────────────────────────────────────────────────

def test_sufficient_frame_passes():
    result = check_preflight_data(_make_frame(25))
    assert result.ok is True
    assert result.reason == ""
    assert result.failed_check is None


# ── Robustness ────────────────────────────────────────────────────────────────

def test_malformed_frame_no_bars_attr():
    """Object without bars attr should fail conservatively."""
    class FakeFrame:
        pass
    result = check_preflight_data(FakeFrame())
    assert result.ok is False


def test_string_input_no_crash():
    """String input should not crash."""
    result = check_preflight_data("not a frame")
    assert result.ok is False


def test_integer_input_no_crash():
    result = check_preflight_data(42)
    assert result.ok is False


# ── Property 1: PreflightDataGate boundary and determinism ────────────────────
# Feature: deterministic-decision-nodes, Property 1: PreflightDataGate boundary & determinism

@st.composite
def valid_bars(draw, n: int) -> tuple[KlineBar, ...]:
    """Generate n valid KlineBars."""
    bars = []
    for i in range(n):
        low = draw(st.floats(min_value=1.0, max_value=9000.0, allow_nan=False, allow_infinity=False))
        high = draw(st.floats(min_value=low, max_value=low + 200.0, allow_nan=False, allow_infinity=False))
        open_ = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
        close = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
        bars.append(KlineBar(
            seq=i + 1,
            ts_open=float(1_000_000 - i * 60_000),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            closed=True,
        ))
    return tuple(bars)


@given(
    n=st.integers(min_value=20, max_value=50),
)
@settings(max_examples=100)
def test_property1_sufficient_frame_passes(n: int) -> None:
    """Property 1: frames with n>=20 valid bars and valid indicators pass preflight."""
    bars = tuple(_make_bar(i + 1) for i in range(n))
    frame = KlineFrame(
        symbol="TEST", timeframe="1h", bars=bars, snapshot_ts_local_ms=1,
        indicators=IndicatorBundle(ema20=tuple([2000.0] * n), atr14=tuple([10.0] * n)),
    )
    r1 = check_preflight_data(frame)
    r2 = check_preflight_data(frame)
    assert r1.ok is True
    assert r2.ok == r1.ok
    assert r2.failed_check == r1.failed_check


@given(
    n=st.integers(min_value=0, max_value=19),
)
@settings(max_examples=100)
def test_property1_insufficient_bars_fails(n: int) -> None:
    """Property 1: frames with n<20 bars fail with bar_count_lt_20."""
    bars = tuple(_make_bar(i + 1) for i in range(n))
    ema = tuple([2000.0] * n)
    atr = tuple([10.0] * n)
    frame = KlineFrame(
        symbol="TEST", timeframe="1h", bars=bars, snapshot_ts_local_ms=1,
        indicators=IndicatorBundle(ema20=ema, atr14=atr),
    )
    r = check_preflight_data(frame)
    if n == 0:
        assert r.failed_check == "bars_empty_or_bad_ohlc"
    else:
        assert r.ok is False
        assert r.failed_check == "bar_count_lt_20"


@given(
    n=st.integers(min_value=20, max_value=50),
)
@settings(max_examples=50)
def test_property1_all_nan_indicators_fails(n: int) -> None:
    """Property 1: frames with n>=20 but all NaN indicators fail with indicators_all_nan."""
    bars = tuple(_make_bar(i + 1) for i in range(n))
    frame = KlineFrame(
        symbol="TEST", timeframe="1h", bars=bars, snapshot_ts_local_ms=1,
        indicators=IndicatorBundle(
            ema20=tuple([float("nan")] * n),
            atr14=tuple([float("nan")] * n),
        ),
    )
    r = check_preflight_data(frame)
    assert r.ok is False
    assert r.failed_check == "indicators_all_nan"


@given(
    n=st.integers(min_value=20, max_value=50),
)
@settings(max_examples=50)
def test_property1_determinism(n: int) -> None:
    """Property 1: same frame produces same result twice (deterministic)."""
    frame = _make_frame(n)
    r1 = check_preflight_data(frame)
    r2 = check_preflight_data(frame)
    assert r1.ok == r2.ok
    assert r1.failed_check == r2.failed_check
    assert r1.reason == r2.reason
