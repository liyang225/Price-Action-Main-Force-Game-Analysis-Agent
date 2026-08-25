"""Retail-layer feature engineering for daily observations.

DRAFT STUDY — NOT FROZEN.

Retail (herd) behavior features combine:

- price position in a look-back window (低位接 vs 高位追),
- volume anomaly relative to prior median (情绪放量 vs 缩量观望),
- intraday shape (upper/lower shadow, close position — emotional extremes),
- small-order flow share (sml_in_flow + mid_in_flow) when available,
- forward excess return (what the herd earns after acting).

``sml_in_flow`` / ``mid_in_flow`` columns are optional.  When absent every
flow feature becomes NaN and downstream rules must treat the row as
``flow_unavailable`` instead of assuming neutrality.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
FLOW_COLUMNS = ("sml_in_flow", "mid_in_flow", "big_in_flow", "super_in_flow", "main_in_flow")


def engineer_retail_features(
    stock_bars: pd.DataFrame,
    sector_bars: pd.DataFrame,
    *,
    forward_days: int = 5,
    volume_window: int = 20,
    range_window: int = 20,
) -> pd.DataFrame:
    """Build retail features for one stock against its primary sector.

    Returns a frame whose index matches ``stock_bars`` rows, containing the
    retail feature set plus the raw date column.  Rows before the look-back
    or after the forward window carry NaN (ineligible).
    """

    stock = _normalise_bars(stock_bars, "stock")
    sector = _normalise_bars(sector_bars, "sector")
    if stock.empty:
        return pd.DataFrame()
    sector_positions = pd.Series(range(len(sector)), index=sector["date"], dtype="int64")
    result = stock.copy(deep=True)
    result = result.reset_index(drop=True)

    # Forward returns: stock absolute and sector-relative excess.
    forward_stock = _forward_return(result["close"], forward_days)
    sector_close_at_stock_dates = result["date"].map(sector.set_index("date")["close"])
    forward_sector = _forward_return(sector_close_at_stock_dates, forward_days)
    result["forward_stock_return"] = forward_stock
    result["forward_sector_return"] = forward_sector
    result["forward_excess_return"] = forward_stock - forward_sector
    result["benchmark_available"] = result["date"].map(sector_positions).notna() & sector_close_at_stock_dates.notna()

    previous_close = result["close"].shift(1)
    result["return_1d"] = _ratio(result["close"], previous_close) - 1.0

    intraday_range = result["high"] - result["low"]
    result["lower_shadow_ratio"] = _ratio(
        result[["open", "close"]].min(axis=1) - result["low"], intraday_range
    )
    result["upper_shadow_ratio"] = _ratio(
        result["high"] - result[["open", "close"]].max(axis=1), intraday_range
    )
    result["close_position"] = _ratio(
        result["close"] - result["low"], intraday_range
    ).clip(0.0, 1.0)

    prior_volume_median = result["volume"].shift(1).rolling(
        volume_window, min_periods=volume_window
    ).median()
    result["volume_ratio_20"] = _ratio(result["volume"], prior_volume_median)

    rolling_low = result["low"].rolling(range_window, min_periods=range_window).min()
    rolling_high = result["high"].rolling(range_window, min_periods=range_window).max()
    width = (rolling_high - rolling_low).replace(0, np.nan)
    result["price_position_20"] = ((result["close"] - rolling_low) / width).clip(0.0, 1.0)
    result["distance_from_high_20"] = result["close"] / rolling_high - 1.0

    # Small-order flow = herd money.  Optional columns; NaN when absent.
    flow_available = all(column in result.columns for column in FLOW_COLUMNS)
    result["flow_available"] = flow_available
    if flow_available:
        for column in FLOW_COLUMNS:
            result[column] = pd.to_numeric(result[column], errors="coerce")
        result["retail_flow"] = (
            result["sml_in_flow"].fillna(0.0) + result["mid_in_flow"].fillna(0.0)
        )
        total_inflow = (
            result["retail_flow"].abs()
            + result["big_in_flow"].fillna(0.0).abs()
            + result["super_in_flow"].fillna(0.0).abs()
        )
        result["retail_flow_share"] = _ratio(
            result["retail_flow"], total_inflow.replace(0, np.nan)
        )
        result["retail_net_direction"] = np.where(
            result["retail_flow"].fillna(0.0) > 0, "in", "out"
        )
    else:
        result["retail_flow"] = np.nan
        result["retail_flow_share"] = np.nan
        result["retail_net_direction"] = pd.NA

    result["zero_range"] = result["high"].eq(result["low"])
    result["forward_window_complete"] = (
        result["forward_excess_return"].notna()
        & result["forward_stock_return"].notna()
    )
    required_ohlcv = result[list(OHLCV_COLUMNS)].notna().all(axis=1)
    result["required_ohlcv_complete"] = required_ohlcv
    result["eligible"] = (
        required_ohlcv
        & result["forward_window_complete"]
        & result["benchmark_available"].fillna(False)
    )
    return result


def _normalise_bars(frame: pd.DataFrame, *, name: str) -> pd.DataFrame:
    result = frame.copy(deep=True)
    if "date" not in result and "time_key" in result:
        result = result.rename(columns={"time_key": "date"})
    if "date" not in result:
        raise ValueError(f"{name} bars are missing columns: date")
    missing = sorted(set(OHLCV_COLUMNS).difference(result.columns))
    for column in missing:
        result[column] = np.nan
    result["date"] = pd.to_datetime(result["date"], errors="raise").dt.tz_localize(None).dt.normalize()
    if result["date"].duplicated().any():
        raise ValueError(f"{name} bars contain duplicate dates")
    for column in OHLCV_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    prices = result[["open", "high", "low", "close"]].stack()
    if (prices.dropna() <= 0).any() or (result["volume"].dropna() < 0).any():
        raise ValueError(f"{name} OHLC prices must be positive and volume non-negative")
    return result.sort_values("date", kind="mergesort").reset_index(drop=True)


def _forward_return(close: pd.Series, periods: int) -> pd.Series:
    return close.shift(-periods) / close - 1.0


def _ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    safe = denominator.replace(0, np.nan)
    return numerator / safe


def _ensure_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = sorted(set(columns).difference(frame.columns))
    if missing:
        raise ValueError(f"frame is missing columns: {', '.join(missing)}")


__all__ = ["engineer_retail_features"]
