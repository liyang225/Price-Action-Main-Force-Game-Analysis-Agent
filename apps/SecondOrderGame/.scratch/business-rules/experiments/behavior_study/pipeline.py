"""End-to-end, reproducible daily behavior-rule study pipeline."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .benchmarks import compare_forward_benchmarks, extreme_market_sample_analysis
from .features import engineer_features
from .rules import FROZEN_LABELS, RuleConfig, evaluate_rule_masks, resolve_fixed_priority
from .stats import overlap_statistics, resolved_distribution


def _rule_config_payload(config: RuleConfig) -> dict[str, Any]:
    return {
        "version": config.version,
        "forward_return_feature": config.forward_return_feature,
        "thresholds": dict(config.thresholds),
        "rules": config.rules,
        "priority": list(config.priority),
    }


def _config_hash(config: RuleConfig) -> str:
    encoded = json.dumps(
        _rule_config_payload(config), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prepare_input(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy(deep=True)
    if "instrument_type" not in result and "asset_type" in result:
        result = result.rename(columns={"asset_type": "instrument_type"})
    if "date" not in result and "time_key" in result:
        result["date"] = result["time_key"]
    if "primary_plate_code" not in result and "sector_code" in result:
        result["primary_plate_code"] = result["sector_code"]
    required = {"code", "instrument_type", "date", "open", "high", "low", "close", "volume"}
    missing = sorted(required.difference(result.columns))
    if missing:
        raise ValueError(f"study data are missing columns: {', '.join(missing)}")
    result["code"] = result["code"].astype(str).str.upper()
    result["instrument_type"] = result["instrument_type"].astype(str).str.lower()
    result["date"] = pd.to_datetime(result["date"], errors="raise").dt.normalize()
    return result


def run_study(
    data: pd.DataFrame,
    rule_config: RuleConfig,
    *,
    forward_days: int = 5,
    volume_window: int = 20,
    negative_threshold: float = -0.03,
    positive_threshold: float = 0.03,
    extreme_market_threshold: float = -0.03,
) -> dict[str, Any]:
    """Run feature, benchmark, mask, overlap and resolution stages.

    Every stock must carry a ``primary_plate_code`` selected by the collector.
    A missing sector series is recorded and the stock is excluded; it is never
    silently replaced with the broad-market benchmark.
    """

    prepared = _prepare_input(data)
    if "primary_plate_code" not in prepared:
        raise ValueError("study data must contain primary_plate_code for sector alignment")
    prepared["primary_plate_code"] = prepared["primary_plate_code"].astype("string").str.upper()
    stocks = prepared.loc[prepared["instrument_type"].eq("stock")]
    sectors = prepared.loc[prepared["instrument_type"].eq("sector")]
    benchmarks = prepared.loc[prepared["instrument_type"].eq("benchmark")]
    market_returns = pd.DataFrame(columns=["date", "market_forward_return"])
    if not benchmarks.empty:
        benchmark_code = sorted(benchmarks["code"].unique().tolist())[0]
        benchmark_bars = benchmarks.loc[benchmarks["code"].eq(benchmark_code)].sort_values("date")
        denominator = pd.to_numeric(benchmark_bars["close"], errors="coerce")
        numerator = denominator.shift(-forward_days)
        market_returns = benchmark_bars[["date"]].copy()
        market_returns["market_forward_return"] = (numerator / denominator) - 1.0
    feature_frames: list[pd.DataFrame] = []
    skipped: list[dict[str, str]] = []

    for stock_code, stock_bars in stocks.groupby("code", sort=True):
        plate_codes = stock_bars["primary_plate_code"].dropna().unique().tolist()
        if len(plate_codes) != 1:
            skipped.append(
                {
                    "code": stock_code,
                    "reason": "stock must have exactly one primary_plate_code",
                }
            )
            continue
        sector_code = str(plate_codes[0])
        sector_bars = sectors.loc[sectors["code"].eq(sector_code)]
        if sector_bars.empty:
            skipped.append({"code": stock_code, "reason": f"missing sector history {sector_code}"})
            continue
        engineered = engineer_features(
            stock_bars,
            sector_bars,
            forward_days=forward_days,
            volume_window=volume_window,
        )
        engineered["stock_code"] = stock_code
        engineered["sector_code"] = sector_code
        if not market_returns.empty:
            engineered = engineered.merge(market_returns, on="date", how="left", validate="many_to_one")
        else:
            # Missing CSI300 data makes the extreme-market diagnostic
            # unavailable; never substitute a sector return for the market.
            engineered["market_forward_return"] = np.nan
        feature_frames.append(engineered)

    if not feature_frames:
        reasons = "; ".join(f"{item['code']}: {item['reason']}" for item in skipped)
        raise ValueError(f"no stock could be aligned to sector history{': ' + reasons if reasons else ''}")
    features = pd.concat(feature_frames, ignore_index=True)
    masks = evaluate_rule_masks(features, rule_config)
    labels = resolve_fixed_priority(masks, rule_config.priority)

    benchmark = compare_forward_benchmarks(
        features,
        negative_threshold=negative_threshold,
        positive_threshold=positive_threshold,
    )
    benchmark_labels = pd.DataFrame(
        {
            "absolute_bucket": benchmark.pop("absolute_labels"),
            "relative_bucket": benchmark.pop("relative_labels"),
        },
        index=features.index,
    )
    extreme = extreme_market_sample_analysis(
        features,
        market_return_threshold=extreme_market_threshold,
        absolute_threshold=negative_threshold,
        relative_threshold=negative_threshold,
        market_return_column="market_forward_return",
    )
    eligible_mask = (
        features[["forward_stock_return", "forward_sector_return", "forward_excess_return"]]
        .notna()
        .all(axis=1)
        & features["benchmark_available"].fillna(False).astype(bool)
    )
    overlap = overlap_statistics(masks.loc[eligible_mask])
    final_distribution = resolved_distribution(labels.loc[eligible_mask])
    shakeout_or_stop_hunt = eligible_mask & (masks["震仓"] | masks["狩猎止损"])
    shakeout_and_stop_hunt = eligible_mask & masks["震仓"] & masks["狩猎止损"]
    separated_union_count = int(shakeout_or_stop_hunt.sum())
    shakeout_stop_hunt = {
        "union_count": separated_union_count,
        "conflict_count": int(shakeout_and_stop_hunt.sum()),
        "conflict_share_of_union": (
            float(shakeout_and_stop_hunt.sum() / separated_union_count)
            if separated_union_count
            else 0.0
        ),
        "boundary_feature": "support_break_pct",
        "shakeout_condition": "> -0.005",
        "stop_hunt_condition": "<= -0.005",
    }
    unmatched_mask = eligible_mask & ~masks.any(axis=1)
    unmatched_count = int(unmatched_mask.sum())
    ineligible_count = int((~eligible_mask).sum())
    eligible_count = int(eligible_mask.sum())
    watch_count = int((labels.loc[eligible_mask] == "观望").sum())
    diagnostic_columns = (
        "return_1d",
        "forward_stock_return",
        "forward_excess_return",
        "volume_ratio_20",
        "range_pct",
        "volatility_20",
    )
    unmatched_features: dict[str, Any] = {}
    for column in diagnostic_columns:
        values = features.loc[unmatched_mask, column].dropna()
        unmatched_features[column] = {
            "count": int(len(values)),
            "mean": float(values.mean()) if len(values) else None,
            "median": float(values.median()) if len(values) else None,
            "q25": float(values.quantile(0.25)) if len(values) else None,
            "q75": float(values.quantile(0.75)) if len(values) else None,
        }
    unmatched_analysis = {
        "eligible_row_count": eligible_count,
        "ineligible_missing_data_count": ineligible_count,
        "count": unmatched_count,
        "share": unmatched_count / eligible_count if eligible_count else 0.0,
        "coverage": 1.0 - (unmatched_count / eligible_count if eligible_count else 0.0),
        "feature_summary": unmatched_features,
        "watch_if_used_as_fallback": {
            "observed_watch_count": watch_count,
            "fallback_watch_count": watch_count + unmatched_count,
            "inflation_multiple": (watch_count + unmatched_count) / watch_count if watch_count else None,
        },
    }
    coverage_rows = features.loc[eligible_mask, ["sector_code", "date"]].copy()
    coverage_rows["matched"] = labels.loc[eligible_mask].notna().to_numpy()

    def coverage_by(column: str) -> dict[str, dict[str, Any]]:
        grouped = coverage_rows.groupby(column, sort=True)["matched"].agg(["size", "sum"])
        return {
            str(key): {
                "eligible_count": int(row["size"]),
                "matched_count": int(row["sum"]),
                "coverage": float(row["sum"] / row["size"]) if row["size"] else 0.0,
            }
            for key, row in grouped.iterrows()
        }

    coverage_rows["year"] = coverage_rows["date"].dt.year
    unmatched_analysis["coverage_by_sector"] = coverage_by("sector_code")
    unmatched_analysis["coverage_by_year"] = coverage_by("year")
    summary = {
        "schema_version": "1",
        "input": {
            "row_count": int(len(prepared)),
            "stock_count": int(stocks["code"].nunique()),
            "sector_count": int(sectors["code"].nunique()),
            "benchmark_count": int(benchmarks["code"].nunique()),
            "feature_row_count": int(len(features)),
            "ineligible_missing_data_count": ineligible_count,
            "ineligible_missing_data_share": ineligible_count / len(features) if len(features) else 0.0,
            "skipped_stocks": skipped,
        },
        "parameters": {
            "forward_days": forward_days,
            "volume_window": volume_window,
            "negative_threshold": negative_threshold,
            "positive_threshold": positive_threshold,
            "extreme_market_threshold": extreme_market_threshold,
        },
        "rules": {
            "version": rule_config.version,
            "sha256": _config_hash(rule_config),
            "forward_return_feature": rule_config.forward_return_feature,
            "priority": list(rule_config.priority),
        },
        "benchmark": benchmark,
        "extreme_market": extreme,
        "overlap": overlap,
        "rule_diagnostics": {"shakeout_vs_stop_hunt": shakeout_stop_hunt},
        "resolved_distribution": final_distribution,
        "unmatched_analysis": unmatched_analysis,
    }
    return {
        "features": features,
        "masks": masks,
        "labels": labels,
        "benchmark_labels": benchmark_labels,
        "summary": summary,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return {
            "index": [str(item) for item in value.index.tolist()],
            "columns": [str(item) for item in value.columns.tolist()],
            "data": [[_jsonable(item) for item in row] for row in value.to_numpy().tolist()],
        }
    if isinstance(value, pd.Series):
        return [_jsonable(item) for item in value.tolist()]
    if isinstance(value, Mapping):
        return {(" + ".join(key) if isinstance(key, tuple) else str(key)): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if value is pd.NA or (isinstance(value, float) and np.isnan(value)):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    return value


def write_study_outputs(study: Mapping[str, Any], output_dir: str | Path) -> dict[str, Path]:
    """Persist derived data and a JSON summary with deterministic gzip mtime."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths = {
        "features": destination / "features.csv.gz",
        "masks": destination / "candidate_masks.csv.gz",
        "labels": destination / "resolved_labels.csv.gz",
        "summary": destination / "summary.json",
    }
    compression = {"method": "gzip", "mtime": 0}
    features = study["features"].copy()
    features.to_csv(paths["features"], index=False, compression=compression, encoding="utf-8")

    keys = features[["stock_code", "sector_code", "date"]].copy()
    mask_output = pd.concat([keys, study["masks"].reset_index(drop=True)], axis=1)
    mask_output["hit_count"] = study["masks"].sum(axis=1).to_numpy()
    mask_output.to_csv(paths["masks"], index=False, compression=compression, encoding="utf-8")

    label_output = keys.copy()
    label_output["behavior_label"] = study["labels"].reset_index(drop=True)
    label_output = pd.concat(
        [label_output, study["benchmark_labels"].reset_index(drop=True)], axis=1
    )
    label_output.to_csv(paths["labels"], index=False, compression=compression, encoding="utf-8")
    paths["summary"].write_text(
        json.dumps(_jsonable(study["summary"]), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return paths


__all__ = ["run_study", "write_study_outputs"]
