"""Tests for explicit 阳/阴 labels in K-line tables."""
from __future__ import annotations

import math

from pa_agent.ai.kline_features import bar_candle_direction_label
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame


def _bar(*, open_: float, close: float, seq: int = 1) -> KlineBar:
    return KlineBar(
        seq=seq,
        ts_open=1_700_000_000_000,
        open=open_,
        high=max(open_, close) + 1.0,
        low=min(open_, close) - 1.0,
        close=close,
        volume=100.0,
        closed=True,
    )


def test_bar_candle_direction_label() -> None:
    assert bar_candle_direction_label(_bar(open_=10.0, close=11.0)) == "阳线"
    assert bar_candle_direction_label(_bar(open_=10.0, close=9.0)) == "阴线"
    assert bar_candle_direction_label(_bar(open_=10.0, close=10.0)) == "平"


def test_kline_table_includes_yang_yin_column() -> None:
    frame = KlineFrame(
        symbol="TEST",
        timeframe="15m",
        bars=(
            _bar(open_=100.0, close=101.0, seq=1),
            _bar(open_=101.0, close=100.0, seq=2),
            _bar(open_=100.0, close=100.0, seq=3),
        ),
        indicators=IndicatorBundle(
            ema20=(math.nan, math.nan, math.nan),
            atr14=(math.nan, math.nan, math.nan),
        ),
        snapshot_ts_local_ms=0,
    )
    table = PromptAssembler._render_kline_table(frame)
    header = table.split("\n")[0]
    assert "阳阴" in header
    assert "阳线" in table
    assert "阴线" in table
    assert "平" in table
