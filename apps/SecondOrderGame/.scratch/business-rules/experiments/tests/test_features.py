from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from behavior_study.features import engineer_features


def _bars(close: list[float], *, high: list[float] | None = None,
          low: list[float] | None = None, volume: list[float] | None = None) -> pd.DataFrame:
    close_series = pd.Series(close, dtype=float)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=len(close), freq="D"),
            "open": close_series.shift(1).fillna(close_series),
            "high": high or [value + 1 for value in close],
            "low": low or [value - 1 for value in close],
            "close": close,
            "volume": volume or [100] * len(close),
        }
    )


def test_engineering_aligns_sector_and_calculates_forward_excess_return() -> None:
    stock = _bars([100, 102, 101, 105, 107, 110, 111])
    sector = _bars([50, 51, 50, 52, 52, 53, 55])

    result = engineer_features(stock, sector, forward_days=2, volume_window=3)

    assert result.loc[0, "forward_stock_return"] == pytest.approx(0.01)
    assert result.loc[0, "forward_sector_return"] == pytest.approx(0.0)
    assert result.loc[0, "forward_excess_return"] == pytest.approx(0.01)
    assert result["date"].is_monotonic_increasing
    assert result["benchmark_available"].all()


def test_zero_range_features_are_finite_and_neutral() -> None:
    stock = _bars([10, 10, 11], high=[10, 10, 12], low=[10, 10, 10])
    sector = _bars([20, 20, 21], high=[20, 20, 22], low=[20, 20, 20])

    result = engineer_features(stock, sector, forward_days=1)

    for column in ("body_ratio", "lower_shadow_ratio", "upper_shadow_ratio", "close_position"):
        assert np.isfinite(result[column].dropna()).all(), column
    assert result.loc[0, "close_position"] == 0.5
    assert result.loc[0, "lower_shadow_ratio"] == 0.0
    assert result.loc[0, "upper_shadow_ratio"] == 0.0


def test_missing_sector_date_is_explicitly_marked_unavailable() -> None:
    stock = _bars([10, 11, 12])
    sector = _bars([20, 21],).iloc[[0, 1]]

    result = engineer_features(stock, sector, forward_days=1)

    assert not bool(result.loc[2, "benchmark_available"])
    assert pd.isna(result.loc[2, "sector_close"])
    assert pd.isna(result.loc[2, "forward_excess_return"])


def test_suspension_gap_does_not_extend_forward_window() -> None:
    sector = _bars([20, 21, 22, 23, 24, 25, 26])
    stock = _bars([10, 11, 12, 13, 14, 15, 16]).drop(index=2).reset_index(drop=True)

    result = engineer_features(stock, sector, forward_days=2)

    assert not bool(result.loc[0, "forward_window_complete"])
    assert pd.isna(result.loc[0, "forward_stock_return"])
    assert bool(result.loc[2, "forward_window_complete"])
    assert result.loc[2, "forward_stock_return"] == pytest.approx(2 / 13)
