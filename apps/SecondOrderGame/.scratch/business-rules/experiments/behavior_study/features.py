"""Feature engineering for stock and sector daily OHLCV observations.

The functions in this module do not fetch data and do not mutate their input
frames.  A feature is ``NaN`` when its information is unavailable (for
example, before a look-back is populated or after the forward window ends).
Ratios based on a zero intraday range use neutral, finite values instead of
producing infinities.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")


def _normalise_bars(frame: pd.DataFrame, *, frame_name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{frame_name} must be a pandas DataFrame")
    result = frame.copy(deep=True)
    aliases = {"time_key": "date", "time": "date"}
    if "date" not in result.columns:
        for alias, canonical in aliases.items():
            if alias in result.columns:
                result = result.rename(columns={alias: canonical})
                break
    required = {"date", *OHLCV_COLUMNS}
    missing = sorted(required.difference(result.columns))
    if missing:
        raise ValueError(f"{frame_name} is missing OHLCV columns: {', '.join(missing)}")

    parsed_dates = pd.to_datetime(result["date"], errors="raise")
    # Merge a timezone-aware and a naive source safely when a provider changes
    # its timestamp representation between endpoints.
    if getattr(parsed_dates.dt, "tz", None) is not None:
        parsed_dates = parsed_dates.dt.tz_localize(None)
    result["date"] = parsed_dates.dt.normalize()
    if result["date"].duplicated().any():
        duplicate = result.loc[result["date"].duplicated(), "date"].iloc[0]
        raise ValueError(f"{frame_name} contains duplicate date {duplicate.date()}")
    for column in OHLCV_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.sort_values("date", kind="mergesort").reset_index(drop=True)
    return result


def _safe_ratio(
    numerator: pd.Series,
    denominator: pd.Series,
    *,
    zero_fill: float | None = np.nan,
) -> pd.Series:
    """Divide without warnings, treating zero denominators explicitly."""

    numerator_values = numerator.to_numpy(dtype=float)
    denominator_values = denominator.to_numpy(dtype=float)
    output = np.full(numerator_values.shape, zero_fill, dtype=float)
    valid = np.isfinite(numerator_values) & np.isfinite(denominator_values) & (denominator_values != 0)
    np.divide(numerator_values, denominator_values, out=output, where=valid)
    return pd.Series(output, index=numerator.index)


def _forward_return(close: pd.Series, periods: int = 1) -> pd.Series:
    if periods < 1:
        raise ValueError("periods must be positive")
    return _safe_ratio(close.shift(-periods), close, zero_fill=np.nan) - 1.0


def _future_extreme(series: pd.Series, window: int, *, reducer: str) -> pd.Series:
    values = series.to_numpy(dtype=float)
    output = np.full(values.shape, np.nan, dtype=float)
    for index in range(len(values)):
        future = values[index + 1 : index + 1 + window]
        if len(future) < window or not np.isfinite(future).all():
            continue
        output[index] = np.min(future) if reducer == "min" else np.max(future)
    return pd.Series(output, index=series.index)


def _prior_rolling(series: pd.Series, window: int, *, method: str) -> pd.Series:
    if window < 1:
        raise ValueError("rolling window must be positive")
    shifted = series.shift(1)
    rolling = getattr(shifted.rolling(window=window, min_periods=window), method)()
    return rolling


def _ensure_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"features are missing required columns: {', '.join(missing)}")


def engineer_features(
    stock: pd.DataFrame,
    sector: pd.DataFrame,
    *,
    forward_days: int = 5,
    volume_window: int = 20,
) -> pd.DataFrame:
    """Join stock/sector bars and calculate leakage-safe study features.

    ``forward_days`` must cover consecutive sector trading sessions.  If the
    stock is suspended inside the window, forward features remain missing
    instead of silently extending the horizon to five later stock bars.
    Sector rows are left-joined: a missing benchmark is retained and marked
    by ``benchmark_available`` so a caller can report or exclude it rather
    than silently replacing it.
    """

    if forward_days < 1:
        raise ValueError("forward_days must be positive")
    stock_bars = _normalise_bars(stock, frame_name="stock")
    sector_bars = _normalise_bars(sector, frame_name="sector")
    sector_positions = pd.Series(
        range(len(sector_bars)), index=sector_bars["date"], dtype="int64"
    )

    sector_columns = ["date", *OHLCV_COLUMNS]
    if "turnover_rate" in sector_bars:
        sector_columns.append("turnover_rate")
    sector_bars = sector_bars[sector_columns].rename(
        columns={column: f"sector_{column}" for column in sector_columns if column != "date"}
    )
    result = stock_bars.merge(sector_bars, on="date", how="left", validate="one_to_one")
    result["benchmark_available"] = result["sector_close"].notna()
    stock_sector_position = result["date"].map(sector_positions)
    result["forward_window_complete"] = (
        stock_sector_position.shift(-forward_days) - stock_sector_position
    ).eq(forward_days)

    result["return_1d"] = _safe_ratio(result["close"], result["close"].shift(1), zero_fill=np.nan) - 1.0
    result["sector_return_1d"] = _safe_ratio(
        result["sector_close"], result["sector_close"].shift(1), zero_fill=np.nan
    ) - 1.0
    result["excess_return_1d"] = result["return_1d"] - result["sector_return_1d"]

    result["forward_stock_return"] = _forward_return(result["close"], forward_days)
    result["forward_sector_return"] = _forward_return(result["sector_close"], forward_days)
    result["forward_excess_return"] = result["forward_stock_return"] - result["forward_sector_return"]
    # Explicit aliases make the benchmark choice visible in config without
    # forcing downstream code to duplicate feature engineering.
    result["forward_return_absolute"] = result["forward_stock_return"]
    result["forward_return_relative"] = result["forward_excess_return"]
    result["forward_relative_return"] = _safe_ratio(
        1.0 + result["forward_stock_return"],
        1.0 + result["forward_sector_return"],
        zero_fill=np.nan,
    ) - 1.0

    intraday_range = result["high"] - result["low"]
    result["range"] = intraday_range
    previous_close = result["close"].shift(1)
    result["range_pct"] = _safe_ratio(intraday_range, previous_close, zero_fill=np.nan)
    result["body_ratio"] = _safe_ratio(
        (result["close"] - result["open"]).abs(), intraday_range, zero_fill=0.0
    )
    lower_shadow = result[["open", "close"]].min(axis=1) - result["low"]
    upper_shadow = result["high"] - result[["open", "close"]].max(axis=1)
    result["lower_shadow_ratio"] = _safe_ratio(lower_shadow, intraday_range, zero_fill=0.0).clip(lower=0.0)
    result["upper_shadow_ratio"] = _safe_ratio(upper_shadow, intraday_range, zero_fill=0.0).clip(lower=0.0)
    # A flat bar has no directional close; the midpoint is a neutral encoding.
    result["close_position"] = _safe_ratio(
        result["close"] - result["low"], intraday_range, zero_fill=0.5
    ).clip(lower=0.0, upper=1.0)
    result["gap_return"] = _safe_ratio(result["open"], previous_close, zero_fill=np.nan) - 1.0

    prior_volume = _prior_rolling(result["volume"], volume_window, method="median")
    result["volume_median_prior"] = prior_volume
    result["volume_ratio_20"] = _safe_ratio(result["volume"], prior_volume, zero_fill=np.nan)
    result["volatility_20"] = result["return_1d"].shift(1).rolling(
        window=volume_window, min_periods=volume_window
    ).std(ddof=0)
    if "turnover_rate" in result:
        prior_turnover = _prior_rolling(result["turnover_rate"], volume_window, method="median")
        result["turnover_ratio_20"] = _safe_ratio(result["turnover_rate"], prior_turnover, zero_fill=np.nan)
    else:
        result["turnover_ratio_20"] = np.nan

    future_low = _future_extreme(result["low"], forward_days, reducer="min")
    future_high = _future_extreme(result["high"], forward_days, reducer="max")
    future_close_high = _future_extreme(result["close"], forward_days, reducer="max")
    future_close_low = _future_extreme(result["close"], forward_days, reducer="min")
    result["future_min_low"] = future_low
    result["future_max_high"] = future_high
    result["future_close_max"] = future_close_high
    result["future_close_min"] = future_close_low
    result["future_rebound_return"] = _safe_ratio(future_close_high, result["close"], zero_fill=np.nan) - 1.0
    result["future_drawdown_return"] = _safe_ratio(future_close_low, result["close"], zero_fill=np.nan) - 1.0

    prior_support = _prior_rolling(result["low"], volume_window, method="min")
    result["prior_support_low"] = prior_support
    result["support_break_pct"] = _safe_ratio(result["low"], prior_support, zero_fill=np.nan) - 1.0
    prior_resistance = _prior_rolling(result["high"], volume_window, method="max")
    result["prior_resistance_high"] = prior_resistance
    result["resistance_break_pct"] = _safe_ratio(
        result["high"], prior_resistance, zero_fill=np.nan
    ) - 1.0
    forward_columns = (
        "forward_stock_return",
        "forward_sector_return",
        "forward_excess_return",
        "forward_return_absolute",
        "forward_return_relative",
        "forward_relative_return",
        "future_min_low",
        "future_max_high",
        "future_close_max",
        "future_close_min",
        "future_rebound_return",
        "future_drawdown_return",
    )
    result.loc[~result["forward_window_complete"], list(forward_columns)] = np.nan
    return result


__all__ = ["OHLCV_COLUMNS", "engineer_features"]
