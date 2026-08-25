"""Absolute versus sector-relative forward-return study functions."""

from __future__ import annotations

from typing import Any

import pandas as pd


RETURN_BUCKETS = ("negative", "neutral", "positive")


def _eligible_rows(features: pd.DataFrame) -> pd.Series:
    required = {"forward_stock_return", "forward_sector_return", "forward_excess_return"}
    missing = sorted(required.difference(features.columns))
    if missing:
        raise ValueError(f"features are missing benchmark columns: {', '.join(missing)}")
    eligible = features[list(required)].notna().all(axis=1)
    if "benchmark_available" in features:
        eligible &= features["benchmark_available"].fillna(False).astype(bool)
    return eligible


def classify_forward_returns(
    values: pd.Series,
    *,
    negative_threshold: float,
    positive_threshold: float,
) -> pd.Series:
    """Bucket returns using strict tail thresholds and preserve missing rows."""

    if negative_threshold >= positive_threshold:
        raise ValueError("negative_threshold must be lower than positive_threshold")
    result = pd.Series(pd.NA, index=values.index, dtype="string")
    valid = values.notna()
    result.loc[valid] = "neutral"
    result.loc[valid & (values < negative_threshold)] = "negative"
    result.loc[valid & (values > positive_threshold)] = "positive"
    return result


def _distribution(labels: pd.Series) -> dict[str, Any]:
    valid = labels.dropna()
    denominator = len(valid)
    result: dict[str, Any] = {}
    counts = valid.value_counts()
    for label in RETURN_BUCKETS:
        count = int(counts.get(label, 0))
        result[label] = {"count": count, "share": count / denominator if denominator else 0.0}
    result["dominant_share"] = (
        max(item["share"] for item in result.values() if isinstance(item, dict)) if denominator else 0.0
    )
    return result


def compare_forward_benchmarks(
    features: pd.DataFrame,
    *,
    negative_threshold: float = -0.03,
    positive_threshold: float = 0.03,
) -> dict[str, Any]:
    """Compare tail buckets under raw and sector-excess five-day returns."""

    eligible = _eligible_rows(features)
    absolute = classify_forward_returns(
        features.loc[eligible, "forward_stock_return"],
        negative_threshold=negative_threshold,
        positive_threshold=positive_threshold,
    )
    relative = classify_forward_returns(
        features.loc[eligible, "forward_excess_return"],
        negative_threshold=negative_threshold,
        positive_threshold=positive_threshold,
    )
    confusion = pd.crosstab(absolute, relative, dropna=False).reindex(
        index=RETURN_BUCKETS, columns=RETURN_BUCKETS, fill_value=0
    )
    return {
        "valid_count": int(eligible.sum()),
        "excluded_missing_benchmark_count": int((~eligible).sum()),
        "absolute": _distribution(absolute),
        "relative": _distribution(relative),
        "confusion": confusion,
        "absolute_labels": absolute,
        "relative_labels": relative,
    }


def extreme_market_sample_analysis(
    features: pd.DataFrame,
    *,
    market_return_threshold: float = -0.03,
    absolute_threshold: float = -0.03,
    relative_threshold: float = -0.03,
    market_return_column: str = "forward_sector_return",
    date_column: str = "date",
) -> dict[str, Any]:
    """Measure raw-return label concentration during sector sell-offs.

    An extreme row is a valid row whose sector forward return is at or below
    ``market_return_threshold``.  The result also quantifies the exact sample
    loss from the alternative "absolute threshold + remove extreme days".
    """

    if market_return_column not in features:
        raise ValueError(f"features are missing market return column {market_return_column!r}")
    eligible = _eligible_rows(features) & features[market_return_column].notna()
    extreme = eligible & (features[market_return_column] <= market_return_threshold)
    absolute_negative = features["forward_stock_return"] < absolute_threshold
    relative_negative = features["forward_excess_return"] < relative_threshold
    count = int(extreme.sum())
    valid_count = int(eligible.sum())

    def share(mask: pd.Series) -> float:
        return float((mask & extreme).sum() / count) if count else 0.0

    result = {
        "valid_count": valid_count,
        "extreme_count": count,
        "extreme_share": count / valid_count if valid_count else 0.0,
        "definition": {
            "column": market_return_column,
            "operator": "<=",
            "threshold": market_return_threshold,
        },
        "absolute": {
            "negative_count": int((absolute_negative & extreme).sum()),
            "negative_share": share(absolute_negative),
        },
        "relative": {
            "negative_count": int((relative_negative & extreme).sum()),
            "negative_share": share(relative_negative),
        },
        "excluded_if_absolute_extreme_filter": {
            "count": count,
            "share": count / valid_count if valid_count else 0.0,
        },
    }
    if date_column in features and count:
        daily = pd.DataFrame(
            {
                "date": features.loc[extreme, date_column],
                "absolute_negative": absolute_negative.loc[extreme].astype(float),
                "relative_negative": relative_negative.loc[extreme].astype(float),
            }
        )
        daily_shares = daily.groupby("date", sort=True)[["absolute_negative", "relative_negative"]].mean()
        result["daily_concentration"] = {
            "date_count": int(len(daily_shares)),
            "absolute_negative_share_mean": float(daily_shares["absolute_negative"].mean()),
            "absolute_negative_share_median": float(daily_shares["absolute_negative"].median()),
            "absolute_negative_share_max": float(daily_shares["absolute_negative"].max()),
            "relative_negative_share_mean": float(daily_shares["relative_negative"].mean()),
            "relative_negative_share_median": float(daily_shares["relative_negative"].median()),
            "relative_negative_share_max": float(daily_shares["relative_negative"].max()),
        }
    else:
        result["daily_concentration"] = {
            "date_count": 0,
            "absolute_negative_share_mean": 0.0,
            "absolute_negative_share_median": 0.0,
            "absolute_negative_share_max": 0.0,
            "relative_negative_share_mean": 0.0,
            "relative_negative_share_median": 0.0,
            "relative_negative_share_max": 0.0,
        }
    return result


__all__ = [
    "RETURN_BUCKETS",
    "classify_forward_returns",
    "compare_forward_benchmarks",
    "extreme_market_sample_analysis",
]
