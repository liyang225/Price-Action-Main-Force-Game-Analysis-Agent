from __future__ import annotations

import math

from pa_agent.indicators.atr import _true_range, atr_full


def test_true_range_non_negative_when_high_less_than_low() -> None:
    assert _true_range(8.0, 16.0, float("nan")) == 8.0


def test_atr_first_bar_non_negative_with_inverted_ohlc() -> None:
    atr = atr_full([8.0], [16.0], [15.0], period=1)
    assert len(atr) == 1
    assert atr[0] >= 0.0
    assert not math.isnan(atr[0])
