"""Frozen, deterministic stock post-hoc labeling from OHLCV and capital flow.

The stock layer owns its rule hash and never reads sector labels or sentiment.
It may read raw sector OHLCV solely to calculate the frozen forward excess
return feature required by ADR-0017.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from src.config_validator import validate_labeler_file
from src.labeler_constants import BEHAVIORS


_DEFAULT_CONFIG_PATH = Path(__file__).parents[2] / "config" / "labeler.yaml"
_OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
_CAPITAL_FLOW_COLUMNS = ("main_in_flow", "super_in_flow", "big_in_flow")
_PARTICIPANT_MAIN_FLOW_SHARE = 0.60


@dataclass(frozen=True, slots=True)
class StockLabelingResult:
    """The deterministic result for every supplied stock day."""

    rows: pd.DataFrame
    features: pd.DataFrame
    candidate_masks: pd.DataFrame
    counts: Counter[str]
    unlabeled_count: int
    unavailable_count: int
    multi_hit_count: int
    coverage: float


def classify_participant(
    capital_flow: Mapping[str, Any] | object | None,
) -> str:
    """Classify a stock day into the frozen two-party participant enum.

    ADR-0018 explicitly uses the large-order share of positive main net flow.
    A non-positive or unavailable main flow follows the specified ``else``
    branch and is classified as retail rather than inventing a third state.
    """

    def value(name: str) -> float:
        if capital_flow is None:
            return float("nan")
        raw = capital_flow.get(name) if isinstance(capital_flow, Mapping) else getattr(capital_flow, name, None)
        try:
            numeric = float(raw)
        except (TypeError, ValueError):
            return float("nan")
        return numeric if np.isfinite(numeric) else float("nan")

    main = value("main_in_flow")
    large = value("super_in_flow") + value("big_in_flow")
    if main > 0 and large / main > _PARTICIPANT_MAIN_FLOW_SHARE:
        return "主力"
    return "散户"


class StockLabeler:
    """Apply the versioned stock-labeler definition without hidden fallbacks."""

    def __init__(self, config_path: Path | str = _DEFAULT_CONFIG_PATH):
        self.config_path = Path(config_path)
        validate_labeler_file(self.config_path)
        self._config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.rule_hash = sha256(self.config_path.read_bytes()).hexdigest()

    @property
    def version(self) -> int:
        return self._config["version"]

    def label(
        self,
        stock_bars: pd.DataFrame,
        sector_bars: pd.DataFrame,
        capital_flows: pd.DataFrame | Sequence[Mapping[str, Any] | object] | None = None,
    ) -> StockLabelingResult:
        """Compute leakage-safe OHLCV features, then label every stock day."""

        features = self.engineer_features(stock_bars, sector_bars, capital_flows)
        return self.label_features(features)

    def engineer_features(
        self,
        stock_bars: pd.DataFrame,
        sector_bars: pd.DataFrame,
        capital_flows: pd.DataFrame | Sequence[Mapping[str, Any] | object] | None = None,
    ) -> pd.DataFrame:
        """Build the configured feature set from one stock and its main sector."""

        stock = _normalise_bars(stock_bars, "stock")
        sector = _normalise_bars(sector_bars, "sector")
        forward_days = self._config["forward_return"]["window_bars"]
        lookback = self._config["lookback"]
        window = lookback["volume_median_bars"]

        sector_positions = pd.Series(range(len(sector)), index=sector["date"], dtype="int64")
        sector = sector.loc[:, ["date", *_OHLCV_COLUMNS]].rename(
            columns={column: f"sector_{column}" for column in _OHLCV_COLUMNS}
        )
        result = stock.merge(sector, on="date", how="left", validate="one_to_one")
        result["benchmark_available"] = result["sector_close"].notna()
        sector_position = result["date"].map(sector_positions)
        result["forward_window_complete"] = (
            sector_position.shift(-forward_days) - sector_position
        ).eq(forward_days)

        previous_close = result["close"].shift(1)
        result["return_1d"] = _ratio(result["close"], previous_close) - 1.0
        result["forward_stock_return"] = _forward_return(result["close"], forward_days)
        result["forward_sector_return"] = _forward_return(result["sector_close"], forward_days)
        result["forward_excess_return"] = (
            result["forward_stock_return"] - result["forward_sector_return"]
        )
        result["forward_absolute_return"] = result["forward_stock_return"]

        intraday_range = result["high"] - result["low"]
        zero_range = intraday_range.eq(0)
        result["lower_shadow_ratio"] = _ratio(
            result[["open", "close"]].min(axis=1) - result["low"], intraday_range
        ).clip(lower=0.0)
        result["upper_shadow_ratio"] = _ratio(
            result["high"] - result[["open", "close"]].max(axis=1), intraday_range
        ).clip(lower=0.0)
        result.loc[zero_range, ["lower_shadow_ratio", "upper_shadow_ratio"]] = self._config[
            "zero_range_bar"
        ]["shadow_ratio"]
        result["close_position"] = _ratio(result["close"] - result["low"], intraday_range).clip(
            lower=0.0, upper=1.0
        )
        result.loc[zero_range, "close_position"] = self._config["zero_range_bar"]["close_position"]

        result["volume_ratio_20"] = _ratio(
            result["volume"], result["volume"].shift(1).rolling(window, min_periods=window).median()
        )
        support = result["low"].shift(1).rolling(
            lookback["support_resistance_bars"], min_periods=lookback["support_resistance_bars"]
        ).min()
        resistance = result["high"].shift(1).rolling(
            lookback["support_resistance_bars"], min_periods=lookback["support_resistance_bars"]
        ).max()
        result["support_break_pct"] = _ratio(result["low"], support) - 1.0
        result["resistance_break_pct"] = _ratio(result["high"], resistance) - 1.0
        result["future_rebound_return"] = _future_max_return(result["close"], forward_days)

        forward_columns = (
            "forward_stock_return", "forward_sector_return", "forward_excess_return",
            "forward_absolute_return", "future_rebound_return"
        )
        result.loc[~result["forward_window_complete"], list(forward_columns)] = np.nan
        return _merge_capital_flows(result, capital_flows)

    def label_features(self, features: pd.DataFrame) -> StockLabelingResult:
        """Label precomputed features, enabling compact stratified golden fixtures."""

        if not isinstance(features, pd.DataFrame):
            raise TypeError("features must be a pandas DataFrame")
        feature_rows = features.copy(deep=True).reset_index(drop=True)
        eligible = _eligible_mask(feature_rows, self._config["forward_return"]["feature"])
        masks = self._candidate_masks(feature_rows).where(eligible, False).astype(bool)
        behavior = pd.Series(pd.NA, index=feature_rows.index, dtype="string")
        for name in self._config["priority"]:
            behavior.loc[behavior.isna() & masks[name]] = name

        status = pd.Series("unavailable", index=feature_rows.index, dtype="string")
        status.loc[eligible] = "unlabeled"
        status.loc[behavior.notna()] = "labeled"
        reason = _status_reasons(feature_rows, eligible, behavior)
        participants = pd.Series(pd.NA, index=feature_rows.index, dtype="string")
        for index in behavior[behavior.notna()].index:
            participants.loc[index] = classify_participant(feature_rows.loc[index])

        rows = pd.DataFrame(
            {
                "date": feature_rows.get("date", pd.Series(pd.NA, index=feature_rows.index)),
                "participant": participants,
                "behavior": behavior,
                "status": status,
                "reason": reason,
                "rule_hash": self.rule_hash,
                "config_version": self.version,
            }
        )
        labeled = behavior.notna()
        eligible_count = int(eligible.sum())
        return StockLabelingResult(
            rows=rows,
            features=feature_rows,
            candidate_masks=masks,
            counts=Counter(behavior.dropna().tolist()),
            unlabeled_count=int((status == "unlabeled").sum()),
            unavailable_count=int((status == "unavailable").sum()),
            multi_hit_count=int((masks.sum(axis=1) > 1).sum()),
            coverage=float(labeled.sum() / eligible_count) if eligible_count else 0.0,
        )

    def _candidate_masks(self, features: pd.DataFrame) -> pd.DataFrame:
        thresholds = self._config["thresholds"]
        forward_feature = self._config["forward_return"]["feature"]
        return pd.DataFrame(
            {
                "建仓": _all(
                    features, thresholds["建仓"],
                    ("return_1d", "gte", "return_1d_min"), ("return_1d", "lte", "return_1d_max"),
                    (forward_feature, "gte", "forward_min"), ("volume_ratio_20", "lte", "volume_ratio_max"),
                    ("close_position", "gte", "close_position_min"),
                ),
                "震仓": _all(
                    features, thresholds["震仓"],
                    ("return_1d", "lte", "return_1d_max"), (forward_feature, "gte", "forward_min"),
                    ("lower_shadow_ratio", "gte", "lower_shadow_min"), ("future_rebound_return", "gte", "rebound_min"),
                    ("support_break_pct", "gt", "support_break_min"), ("volume_ratio_20", "lte", "volume_ratio_max"),
                ),
                "拉升": _all(
                    features, thresholds["拉升"],
                    ("return_1d", "gte", "return_1d_min"), (forward_feature, "gte", "forward_min"),
                    ("volume_ratio_20", "gte", "volume_ratio_min"), ("close_position", "gte", "close_position_min"),
                    ("resistance_break_pct", "gte", "resistance_break_min"),
                ),
                "出货": _all(
                    features, thresholds["出货"],
                    ("return_1d", "gte", "return_1d_min"), ("return_1d", "lte", "return_1d_max"),
                    (forward_feature, "lte", "forward_max"), ("volume_ratio_20", "gte", "volume_ratio_min"),
                    ("upper_shadow_ratio", "gte", "upper_shadow_min"), ("resistance_break_pct", "gte", "resistance_break_min"),
                ),
                "观望": _all(
                    features, thresholds["观望"],
                    ("return_1d", "abs_lte", "return_1d_abs_max"), (forward_feature, "abs_lte", "forward_abs_max"),
                    ("volume_ratio_20", "lte", "volume_ratio_max"),
                ),
                "狩猎止损": _all(
                    features, thresholds["狩猎止损"],
                    ("return_1d", "lte", "return_1d_max"), (forward_feature, "gte", "forward_min"),
                    ("support_break_pct", "lte", "support_break_max"), ("lower_shadow_ratio", "gte", "lower_shadow_min"),
                    ("future_rebound_return", "gte", "rebound_min"), ("volume_ratio_20", "gte", "volume_ratio_min"),
                ),
            },
            index=features.index,
        )


def _normalise_bars(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{name} bars must be a pandas DataFrame")
    result = frame.copy(deep=True)
    if "date" not in result:
        for alias in ("time_key", "time"):
            if alias in result:
                result = result.rename(columns={alias: "date"})
                break
    required = {"date", *_OHLCV_COLUMNS}
    missing = sorted(required.difference(result.columns))
    if missing:
        raise ValueError(f"{name} bars are missing columns: {', '.join(missing)}")
    result["date"] = pd.to_datetime(result["date"], errors="raise").dt.tz_localize(None).dt.normalize()
    if result["date"].duplicated().any():
        raise ValueError(f"{name} bars contain duplicate dates")
    for column in _OHLCV_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.sort_values("date", kind="mergesort").reset_index(drop=True)


def _merge_capital_flows(
    features: pd.DataFrame, capital_flows: pd.DataFrame | Sequence[Mapping[str, Any] | object] | None
) -> pd.DataFrame:
    if capital_flows is None:
        return features
    if isinstance(capital_flows, pd.DataFrame):
        flows = capital_flows.copy(deep=True)
    else:
        flows = pd.DataFrame(
            [
                item if isinstance(item, Mapping) else {
                    name: getattr(item, name, None)
                    for name in ("date", *_CAPITAL_FLOW_COLUMNS)
                }
                for item in capital_flows
            ]
        )
    if "date" not in flows and "time_key" in flows:
        flows = flows.rename(columns={"time_key": "date"})
    required = {"date", *_CAPITAL_FLOW_COLUMNS}
    missing = sorted(required.difference(flows.columns))
    if missing:
        raise ValueError(f"capital flows are missing columns: {', '.join(missing)}")
    flows_have_code = "code" in flows
    if flows_have_code and "code" not in features:
        raise ValueError("stock bars need a code column when capital flows contain codes")
    join_columns = ["date"]
    if flows_have_code:
        join_columns.append("code")
    flows = flows.loc[:, [*join_columns, *_CAPITAL_FLOW_COLUMNS]].copy()
    flows["date"] = pd.to_datetime(flows["date"], errors="raise").dt.tz_localize(None).dt.normalize()
    if flows[join_columns].duplicated().any():
        raise ValueError("capital flows contain duplicate stock-day keys")
    return features.merge(flows, on=join_columns, how="left", validate="one_to_one")


def _ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numerator_values = pd.to_numeric(numerator, errors="coerce").to_numpy(dtype=float)
    denominator_values = pd.to_numeric(denominator, errors="coerce").to_numpy(dtype=float)
    values = np.full(numerator_values.shape, np.nan, dtype=float)
    valid = np.isfinite(numerator_values) & np.isfinite(denominator_values) & (denominator_values != 0)
    np.divide(numerator_values, denominator_values, out=values, where=valid)
    return pd.Series(values, index=numerator.index)


def _forward_return(close: pd.Series, periods: int) -> pd.Series:
    return _ratio(close.shift(-periods), close) - 1.0


def _future_max_return(close: pd.Series, window: int) -> pd.Series:
    values = pd.to_numeric(close, errors="coerce").to_numpy(dtype=float)
    output = np.full(values.shape, np.nan, dtype=float)
    for index in range(len(values)):
        future = values[index + 1 : index + 1 + window]
        if len(future) == window and np.isfinite(future).all() and values[index] != 0:
            output[index] = future.max() / values[index] - 1.0
    return pd.Series(output, index=close.index)


def _eligible_mask(features: pd.DataFrame, forward_feature: str) -> pd.Series:
    required = ("forward_stock_return", "forward_sector_return", forward_feature)
    available = pd.Series(True, index=features.index)
    for column in required:
        available &= _numeric_column(features, column).notna()
    benchmark = features.get("benchmark_available", pd.Series(False, index=features.index))
    window = features.get("forward_window_complete", pd.Series(False, index=features.index))
    return available & benchmark.fillna(False).astype(bool) & window.fillna(False).astype(bool)


def _status_reasons(
    features: pd.DataFrame, eligible: pd.Series, behavior: pd.Series
) -> pd.Series:
    reason = pd.Series(pd.NA, index=features.index, dtype="string")
    reason.loc[eligible & behavior.isna()] = "no_rule_match"
    benchmark = features.get("benchmark_available", pd.Series(False, index=features.index))
    reason.loc[~benchmark.fillna(False).astype(bool)] = "missing_benchmark"
    window = features.get("forward_window_complete", pd.Series(False, index=features.index))
    reason.loc[benchmark.fillna(False).astype(bool) & ~window.fillna(False).astype(bool)] = (
        "incomplete_forward_window"
    )
    reason.loc[~eligible & reason.isna()] = "missing_forward_data"
    return reason


def _numeric_column(features: pd.DataFrame, column: str) -> pd.Series:
    if column not in features:
        return pd.Series(np.nan, index=features.index, dtype=float)
    return pd.to_numeric(features[column], errors="coerce")


def _all(
    features: pd.DataFrame, thresholds: Mapping[str, float], *conditions: tuple[str, str, str]
) -> pd.Series:
    result = pd.Series(True, index=features.index, dtype=bool)
    for feature, operator, threshold_name in conditions:
        values = _numeric_column(features, feature)
        threshold = thresholds[threshold_name]
        if operator == "gte":
            condition = values >= threshold
        elif operator == "gt":
            condition = values > threshold
        elif operator == "lte":
            condition = values <= threshold
        elif operator == "abs_lte":
            condition = values.abs() <= threshold
        else:  # pragma: no cover - all operators are declared above.
            raise ValueError(f"unknown rule operator {operator!r}")
        result &= condition.fillna(False)
    return result


__all__ = ["StockLabeler", "StockLabelingResult", "classify_participant"]
