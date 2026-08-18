"""Property-based tests for EMA and ATR incremental == full (task 5.4 / PR8)."""
from __future__ import annotations

import math
import pytest
from hypothesis import given, assume, settings as h_settings
from hypothesis import strategies as st
from pa_agent.indicators.ema import ema_full, ema_incremental, make_ema_state, state_after
from pa_agent.indicators.atr import atr_full, atr_incremental, make_atr_state, state_after_atr

# ── Helpers ───────────────────────────────────────────────────────────────────

_PRICE = st.floats(min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False)
_PERIOD = st.integers(min_value=2, max_value=20)


# ── EMA tests ─────────────────────────────────────────────────────────────────

@given(
    values=st.lists(_PRICE, min_size=2, max_size=60),
    period=_PERIOD,
    x=_PRICE,
)
@h_settings(max_examples=300)
def test_ema_incremental_matches_full_at_last(
    values: list[float], period: int, x: float
) -> None:
    """ema_full(values + [x])[-1] == ema_incremental(state_after(values), x).last

    **Validates: Requirements PR8.1**
    """
    assume(len(values) >= period)
    full_result = ema_full(values + [x], period)
    expected = full_result[-1]

    state = state_after(values, period)
    incremental_result = ema_incremental(state, x)

    if math.isnan(expected):
        assert math.isnan(incremental_result.last)
    else:
        assert abs(incremental_result.last - expected) < 1e-9, (
            f"EMA mismatch: full={expected}, incremental={incremental_result.last}"
        )


@given(
    values=st.lists(_PRICE, min_size=2, max_size=60),
    period=_PERIOD,
)
@h_settings(max_examples=300)
def test_ema_nan_positions_stable(values: list[float], period: int) -> None:
    """NaN appears exactly in positions 0..period-2; no NaN after that.

    **Validates: Requirements PR8.1**
    """
    result = ema_full(values, period)
    for i, v in enumerate(result):
        if i < period - 1:
            assert math.isnan(v), f"Expected NaN at index {i}, got {v}"
        elif i >= period - 1 and len(values) >= period:
            assert not math.isnan(v), f"Unexpected NaN at index {i}"


@given(
    values=st.lists(_PRICE, min_size=2, max_size=60),
    period=_PERIOD,
)
@h_settings(max_examples=200)
def test_ema_deterministic(values: list[float], period: int) -> None:
    """Same input always produces same output.

    **Validates: Requirements PR8.1**
    """
    r1 = ema_full(values, period)
    r2 = ema_full(values, period)
    assert r1 == r2


# ── ATR tests ─────────────────────────────────────────────────────────────────

@given(
    n=st.integers(min_value=2, max_value=60),
    period=_PERIOD,
    extra_h=_PRICE,
    extra_l_offset=st.floats(min_value=0.0, max_value=5.0, allow_nan=False),
    extra_c=_PRICE,
)
@h_settings(max_examples=300)
def test_atr_incremental_matches_full_at_last(
    n: int, period: int, extra_h: float, extra_l_offset: float, extra_c: float
) -> None:
    """atr_full(H+[h], L+[l], C+[c])[-1] == atr_incremental(state, h, l, c).last

    **Validates: Requirements PR8.1**
    """
    assume(n >= period)
    # Generate synthetic OHLC data
    import random
    rng = random.Random(42)
    closes = [rng.uniform(1.0, 100.0) for _ in range(n)]
    highs  = [c + rng.uniform(0.0, 5.0) for c in closes]
    lows   = [c - rng.uniform(0.0, 5.0) for c in closes]

    extra_l = max(0.01, extra_h - extra_l_offset)

    full_result = atr_full(highs + [extra_h], lows + [extra_l], closes + [extra_c], period)
    expected = full_result[-1]

    state = state_after_atr(highs, lows, closes, period)
    incremental_result = atr_incremental(state, extra_h, extra_l, extra_c)

    if math.isnan(expected):
        assert math.isnan(incremental_result.last)
    else:
        assert abs(incremental_result.last - expected) < 1e-9, (
            f"ATR mismatch: full={expected}, incremental={incremental_result.last}"
        )


@given(
    n=st.integers(min_value=2, max_value=60),
    period=_PERIOD,
)
@h_settings(max_examples=200)
def test_atr_nan_positions_stable(n: int, period: int) -> None:
    """NaN appears exactly in positions 0..period-2.

    **Validates: Requirements PR8.1**
    """
    import random
    rng = random.Random(99)
    closes = [rng.uniform(1.0, 100.0) for _ in range(n)]
    highs  = [c + rng.uniform(0.0, 5.0) for c in closes]
    lows   = [c - rng.uniform(0.0, 5.0) for c in closes]

    result = atr_full(highs, lows, closes, period)
    for i, v in enumerate(result):
        if i < period - 1:
            assert math.isnan(v), f"Expected NaN at index {i}, got {v}"
        elif i >= period - 1 and n >= period:
            assert not math.isnan(v), f"Unexpected NaN at index {i}"
