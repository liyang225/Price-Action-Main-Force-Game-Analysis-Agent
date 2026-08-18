"""Unit tests for cycle display helpers."""
from __future__ import annotations

from pa_agent.ai.cycle_enums import format_cycle_with_direction, format_trend_label


def test_format_trend_label_range_cycles_show_directional_bias() -> None:
    assert format_trend_label("bearish", "trading_range") == "震荡偏空"
    assert format_trend_label("bullish", "trading_range") == "震荡偏多"
    assert format_trend_label("neutral", "trading_range") == "震荡"
    assert format_trend_label("bearish", "trending_tr") == "震荡偏空"
    assert format_trend_label(None, "extreme_tr") == "震荡"


def test_format_trend_label_channels_use_direction() -> None:
    assert format_trend_label("bullish", "broad_channel") == "上涨"
    assert format_trend_label("bearish", "normal_channel") == "下跌"
    assert format_trend_label("neutral", "normal_channel") == "震荡"


def test_format_trend_label_spike_without_direction() -> None:
    assert format_trend_label("", "spike") == "趋势运行中"


def test_range_trend_and_cycle_labels_align() -> None:
    direction = "bearish"
    cycle = "trading_range"
    assert format_trend_label(direction, cycle) == "震荡偏空"
    assert format_cycle_with_direction(cycle, direction) == "下跌交易区间"
