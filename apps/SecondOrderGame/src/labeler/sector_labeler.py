"""Frozen, deterministic post-hoc labeling for sector-index OHLCV."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from src.config_validator import validate_sector_labeler_file


_DEFAULT_CONFIG_PATH = Path(__file__).parents[2] / "config" / "sector_labeler.yaml"
_OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
_FEATURE_COLUMNS = (
    "return_1d",
    "forward_return",
    "volume_ratio_20",
    "volatility_20",
    "recent_trend_5d",
    "consecutive_down_days",
    "consecutive_shrink_days",
    "price_position_20",
)


@dataclass(frozen=True, slots=True)
class SectorLabelingResult:
    """The deterministic result for every supplied sector day."""

    rows: pd.DataFrame
    features: pd.DataFrame
    candidate_masks: pd.DataFrame
    counts: Counter[str]
    unlabeled_count: int
    data_insufficient_count: int
    multi_hit_count: int
    coverage: float


class SectorLabeler:
    """Apply the frozen sector cycle definition without hidden fallbacks."""

    def __init__(self, config_path: Path | str = _DEFAULT_CONFIG_PATH):
        self.config_path = Path(config_path)
        validate_sector_labeler_file(self.config_path)
        self._config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.rule_hash = self._config["rule_hash"]["frozen_hash"]
        self.version = self._config["version"]

    def label(self, sector_bars: pd.DataFrame) -> SectorLabelingResult:
        """Engineer features from sector OHLCV, then assign one cycle state."""

        return self.label_features(self.engineer_features(sector_bars))

    def engineer_features(self, sector_bars: pd.DataFrame) -> pd.DataFrame:
        frame = _normalise_bars(sector_bars, allow_incomplete=True)
        forbidden = [
            column for column in frame.columns
            if "sentiment" in column.lower() or "情绪" in column
        ]
        if forbidden:
            raise ValueError(f"sector sentiment inputs are forbidden: {', '.join(forbidden)}")

        lookback = self._config["lookback"]
        forward_window = int(self._config["forward_return"]["window_bars"])
        volume_window = int(lookback["volume_median_bars"])
        range_window = int(lookback["range_bars"])
        volatility_window = int(lookback["volatility_bars"])

        result = frame.copy(deep=True)
        result["return_1d"] = result["close"].pct_change(fill_method=None)
        result["forward_return"] = result["close"].shift(-forward_window) / result["close"] - 1.0
        prior_volume_median = result["volume"].shift(1).rolling(
            volume_window, min_periods=volume_window
        ).median()
        result["volume_ratio_20"] = result["volume"] / prior_volume_median.replace(0, np.nan)
        result["volatility_20"] = result["return_1d"].rolling(
            volatility_window, min_periods=volatility_window
        ).std(ddof=1)
        result["recent_trend_5d"] = result["close"].shift(1) / result["close"].shift(6) - 1.0
        rolling_low = result["low"].rolling(range_window, min_periods=range_window).min()
        rolling_high = result["high"].rolling(range_window, min_periods=range_window).max()
        width = (rolling_high - rolling_low).replace(0, np.nan)
        result["price_position_20"] = ((result["close"] - rolling_low) / width).clip(0.0, 1.0)
        result["consecutive_down_days"] = _run_length(result["return_1d"] < 0)
        result["consecutive_shrink_days"] = _run_length(result["volume"] < prior_volume_median)
        result["zero_range"] = result["high"].eq(result["low"])
        result["forward_window_complete"] = result["forward_return"].notna()
        return result

    def label_features(self, features: pd.DataFrame) -> SectorLabelingResult:
        if not isinstance(features, pd.DataFrame):
            raise TypeError("features must be a pandas DataFrame")
        frame = features.copy(deep=True).reset_index(drop=True)
        masks = self._candidate_masks(frame)
        eligible = _eligible_mask(frame)
        masks = masks.where(eligible, False).astype(bool)
        labels = pd.Series(pd.NA, index=frame.index, dtype="string")
        for state in self._config["priority"]:
            labels.loc[labels.isna() & masks[state]] = state

        status = pd.Series("data_insufficient", index=frame.index, dtype="string")
        status.loc[eligible] = "unlabeled"
        status.loc[labels.notna()] = "labeled"
        reason = pd.Series(pd.NA, index=frame.index, dtype="string")
        reason.loc[eligible & labels.isna()] = "no_rule_match"
        reason.loc[~eligible] = _insufficiency_reasons(frame.loc[~eligible])

        evidence = pd.Series(pd.NA, index=frame.index, dtype="string")
        expansion = pd.Series(pd.NA, index=frame.index, dtype="boolean")
        for state, metadata in self._config["state_metadata"].items():
            selected = labels.eq(state)
            evidence.loc[selected] = metadata["evidence_mode"]
            expansion.loc[selected] = metadata["expansion_verified"]

        rows = pd.DataFrame(
            {
                "date": frame.get("date", pd.Series(pd.NA, index=frame.index)),
                "cycle_position": labels,
                "status": status,
                "reason": reason,
                "evidence_mode": evidence,
                "expansion_verified": expansion,
                "rule_hash": self.rule_hash,
                "config_version": self.version,
            }
        )
        eligible_count = int(eligible.sum())
        return SectorLabelingResult(
            rows=rows,
            features=frame,
            candidate_masks=masks,
            counts=Counter(labels.dropna().tolist()),
            unlabeled_count=int(status.eq("unlabeled").sum()),
            data_insufficient_count=int(status.eq("data_insufficient").sum()),
            multi_hit_count=int((masks.sum(axis=1) > 1).sum()),
            coverage=float(labels.notna().sum() / eligible_count) if eligible_count else 0.0,
        )

    def _candidate_masks(self, features: pd.DataFrame) -> pd.DataFrame:
        thresholds = self._config["thresholds"]
        return pd.DataFrame(
            {
                "冰点": _all(features, thresholds["冰点"],
                    ("price_position_20", "lte", "price_position_20_max"),
                    ("consecutive_shrink_days", "gte", "consecutive_shrink_days_min"),
                    ("recent_trend_5d", "lte", "recent_trend_5d_max"),
                    ("forward_return", "gte", "forward_min"),
                    ("volume_ratio_20", "lte", "volume_ratio_max")),
                "启动": _all(features, thresholds["启动"],
                    ("return_1d", "gte", "return_1d_min"),
                    ("forward_return", "gte", "forward_min"),
                    ("volume_ratio_20", "gte", "volume_ratio_min"),
                    ("volume_ratio_20", "lte", "volume_ratio_max"),
                    ("price_position_20", "gte", "price_position_20_min"),
                    ("consecutive_down_days", "lte", "consecutive_down_days_max"),
                    ("recent_trend_5d", "lte", "recent_trend_5d_max")),
                "发酵": _all(features, thresholds["发酵"],
                    ("return_1d", "gte", "return_1d_min"),
                    ("forward_return", "gte", "forward_min"),
                    ("volume_ratio_20", "gte", "volume_ratio_min"),
                    ("volume_ratio_20", "lte", "volume_ratio_max"),
                    ("price_position_20", "gte", "price_position_20_min"),
                    ("consecutive_down_days", "lte", "consecutive_down_days_max"),
                    ("recent_trend_5d", "gte", "recent_trend_5d_min")),
                "高潮": _all(features, thresholds["高潮"],
                    ("return_1d", "gte", "return_1d_min"),
                    ("volume_ratio_20", "gte", "volume_ratio_min"),
                    ("price_position_20", "gte", "price_position_20_min"),
                    ("forward_return", "lte", "forward_max"),
                    ("recent_trend_5d", "gte", "recent_trend_5d_min")),
                "退潮": _all(features, thresholds["退潮"],
                    ("return_1d", "lte", "return_1d_max"),
                    ("forward_return", "lte", "forward_max"),
                    ("volume_ratio_20", "gte", "volume_ratio_min"),
                    ("price_position_20", "lte", "price_position_20_max"),
                    ("consecutive_down_days", "gte", "consecutive_down_days_min")),
            }, index=features.index,
        )


def _normalise_bars(data: pd.DataFrame, *, allow_incomplete: bool) -> pd.DataFrame:
    if not isinstance(data, pd.DataFrame):
        raise TypeError("sector bars must be a pandas DataFrame")
    result = data.copy(deep=True)
    if "date" not in result and "time_key" in result:
        result = result.rename(columns={"time_key": "date"})
    if "date" not in result:
        raise ValueError("sector bars are missing columns: date")
    missing = sorted(set(_OHLCV_COLUMNS).difference(result.columns))
    for column in missing:
        result[column] = np.nan
    result["date"] = pd.to_datetime(result["date"], errors="raise").dt.tz_localize(None).dt.normalize()
    if result["date"].duplicated().any():
        raise ValueError("sector bars contain duplicate dates")
    for column in _OHLCV_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["required_ohlcv_complete"] = result[list(_OHLCV_COLUMNS)].notna().all(axis=1)
    prices = result[["open", "high", "low", "close"]].stack()
    if (prices.dropna() <= 0).any() or (result["volume"].dropna() < 0).any():
        raise ValueError("sector OHLC prices must be positive and volume non-negative")
    return result.sort_values("date", kind="mergesort").reset_index(drop=True)


def _eligible_mask(features: pd.DataFrame) -> pd.Series:
    available = features.get("required_ohlcv_complete", pd.Series(False, index=features.index)).fillna(False)
    available &= features.get("forward_window_complete", pd.Series(False, index=features.index)).fillna(False)
    for column in _FEATURE_COLUMNS:
        if column not in features:
            return pd.Series(False, index=features.index)
        available &= pd.to_numeric(features[column], errors="coerce").notna()
    if "zero_range" in features and features["zero_range"].fillna(False).any():
        available &= ~features["zero_range"].fillna(False)
    return available.astype(bool)


def _insufficiency_reasons(features: pd.DataFrame) -> pd.Series:
    reasons = pd.Series("missing_rule_window", index=features.index, dtype="string")
    incomplete_ohlcv = ~features.get("required_ohlcv_complete", pd.Series(False, index=features.index)).fillna(False)
    reasons.loc[incomplete_ohlcv] = "missing_ohlcv"
    forward = features.get("forward_window_complete", pd.Series(False, index=features.index)).fillna(False)
    reasons.loc[~forward & ~incomplete_ohlcv] = "incomplete_forward_window"
    zero_range = features.get("zero_range", pd.Series(False, index=features.index)).fillna(False)
    reasons.loc[zero_range & forward & ~incomplete_ohlcv] = "zero_range_bar"
    return reasons


def _run_length(condition: pd.Series) -> pd.Series:
    values: list[int] = []
    current = 0
    for item in condition.fillna(False).astype(bool):
        current = current + 1 if item else 0
        values.append(current)
    return pd.Series(values, index=condition.index, dtype="int64")


def _all(features: pd.DataFrame, thresholds: Mapping[str, float], *conditions: tuple[str, str, str]) -> pd.Series:
    result = pd.Series(True, index=features.index, dtype=bool)
    for feature, operator, threshold_name in conditions:
        values = _numeric_column(features, feature)
        threshold = thresholds[threshold_name]
        if operator == "gte":
            current = values >= threshold
        elif operator == "lte":
            current = values <= threshold
        else:  # pragma: no cover
            raise ValueError(f"unsupported rule operator {operator}")
        result &= current.fillna(False)
    return result


def _numeric_column(features: pd.DataFrame, column: str) -> pd.Series:
    if column not in features:
        return pd.Series(np.nan, index=features.index, dtype=float)
    return pd.to_numeric(features[column], errors="coerce")


__all__ = ["SectorLabeler", "SectorLabelingResult"]
