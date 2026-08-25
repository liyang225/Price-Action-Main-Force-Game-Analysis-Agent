"""Run the retail behavior rule study and emit a DRAFT candidate report.

DRAFT STUDY — NOT FROZEN (ADR-0007).

Usage:
    python experiments/run_retail_study.py \
        --input ../business-rules/experiments/output/daily_ohlcv.csv.gz \
        --rules config/retail_rules_draft.yaml \
        --output output/retail_study_summary.json

The study reuses the main-force OHLCV dataset.  Small-order flow columns are
NOT present in that archive, so every row is ``flow_unavailable``; the report
documents coverage under OHLCV-only evidence and flags the flow gap for the
follow-up collection ticket.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from retail_features import engineer_retail_features
from retail_rules import evaluate_rule_masks, load_retail_rule_config, resolve_fixed_priority


ROOT = Path(__file__).resolve().parent


def _prepare_input(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy(deep=True)
    if "instrument_type" not in result and "asset_type" in result:
        result = result.rename(columns={"asset_type": "instrument_type"})
    if "date" not in result and "time_key" in result:
        result["date"] = result["time_key"]
    required = {"code", "instrument_type", "date", "open", "high", "low", "close", "volume"}
    missing = sorted(required.difference(result.columns))
    if missing:
        raise ValueError(f"study data are missing columns: {', '.join(missing)}")
    result["code"] = result["code"].astype(str).str.upper()
    result["instrument_type"] = result["instrument_type"].astype(str).str.lower()
    result["date"] = pd.to_datetime(result["date"], errors="raise").dt.normalize()
    return result


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is pd.NA or (isinstance(value, float) and np.isnan(value)):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    return value


def run_retail_study(
    data: pd.DataFrame,
    rule_config: Any,
    *,
    forward_days: int = 5,
    volume_window: int = 20,
    range_window: int = 20,
) -> dict[str, Any]:
    prepared = _prepare_input(data)
    stocks = prepared.loc[prepared["instrument_type"].eq("stock")]
    sectors = prepared.loc[prepared["instrument_type"].eq("sector")]
    feature_frames: list[pd.DataFrame] = []
    skipped: list[dict[str, str]] = []

    for stock_code, stock_bars in stocks.groupby("code", sort=True):
        plate_codes = stock_bars["sector_code"].dropna().unique().tolist()
        if len(plate_codes) != 1:
            skipped.append({"code": stock_code, "reason": "stock must have exactly one sector_code"})
            continue
        sector_code = str(plate_codes[0])
        sector_bars = sectors.loc[sectors["code"].eq(sector_code)]
        if sector_bars.empty:
            skipped.append({"code": stock_code, "reason": f"missing sector history {sector_code}"})
            continue
        engineered = engineer_retail_features(
            stock_bars,
            sector_bars,
            forward_days=forward_days,
            volume_window=volume_window,
            range_window=range_window,
        )
        if engineered.empty:
            continue
        engineered["stock_code"] = stock_code
        engineered["sector_code"] = sector_code
        feature_frames.append(engineered)

    if not feature_frames:
        raise ValueError("no stock could be aligned to sector history")
    features = pd.concat(feature_frames, ignore_index=True)
    masks = evaluate_rule_masks(features, rule_config)
    labels = resolve_fixed_priority(masks, rule_config.priority)

    eligible = features["eligible"].fillna(False).astype(bool)
    flow_available = eligible & features["flow_available"].fillna(False).astype(bool)
    ohlcv_only = eligible & ~flow_available

    # Distribution under the only currently testable evidence mode.
    distribution = Counter(labels.loc[ohlcv_only].dropna().tolist())
    eligible_count = int(ohlcv_only.sum())
    coverage = (
        sum(distribution.values()) / eligible_count if eligible_count else 0.0
    )
    overlap = int((masks.loc[ohlcv_only].sum(axis=1) >= 2).sum())

    per_label: dict[str, Any] = {}
    for label in rule_config.priority:
        count = int((labels.loc[ohlcv_only] == label).sum())
        per_label[label] = {
            "count": count,
            "share": count / eligible_count if eligible_count else 0.0,
        }

    unmatched = ohlcv_only & labels.isna()
    unmatched_features: dict[str, Any] = {}
    for column in ("return_1d", "forward_excess_return", "volume_ratio_20", "price_position_20", "close_position"):
        values = features.loc[unmatched, column].dropna()
        unmatched_features[column] = {
            "count": int(len(values)),
            "median": float(values.median()) if len(values) else None,
            "q25": float(values.quantile(0.25)) if len(values) else None,
            "q75": float(values.quantile(0.75)) if len(values) else None,
        }

    config_payload = {
        "version": rule_config.version,
        "draft": rule_config.draft,
        "forward_return_feature": rule_config.forward_return_feature,
        "evidence": dict(rule_config.evidence),
        "thresholds": dict(rule_config.thresholds),
        "rules": rule_config.rules,
        "priority": list(rule_config.priority),
    }
    config_hash = hashlib.sha256(
        json.dumps(config_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return {
        "features": features,
        "masks": masks,
        "labels": labels,
        "summary": {
            "schema_version": "1",
            "draft": True,
            "input": {
                "row_count": int(len(prepared)),
                "stock_count": int(stocks["code"].nunique()),
                "sector_count": int(sectors["code"].nunique()),
                "feature_row_count": int(len(features)),
                "eligible_ohlcv_only_count": eligible_count,
                "flow_available_count": int(flow_available.sum()),
                "skipped_stocks": skipped,
            },
            "evidence": {
                "mode": "ohlcv_only",
                "flow_columns_present": bool(features["flow_available"].any()),
                "flow_gap_note": (
                    "小单资金流历史数据缺失（资本流台账仅 40 日滚动窗口）；"
                    "本报告基于 OHLCV 证据，散单流证据待采集后补充验证"
                ),
            },
            "parameters": {
                "forward_days": forward_days,
                "volume_window": volume_window,
                "range_window": range_window,
            },
            "rules": {
                "version": rule_config.version,
                "draft": rule_config.draft,
                "sha256": config_hash,
                "forward_return_feature": rule_config.forward_return_feature,
            },
            "resolved_distribution": dict(distribution),
            "coverage": {
                "eligible_count": eligible_count,
                "labeled_count": int(sum(distribution.values())),
                "coverage": coverage,
                "overlap_count": overlap,
                "per_label": per_label,
            },
            "unmatched_analysis": {
                "count": int(unmatched.sum()),
                "share": float(unmatched.sum() / eligible_count) if eligible_count else 0.0,
                "feature_summary": unmatched_features,
            },
        },
    }


def write_retail_outputs(study: Mapping[str, Any], output_dir: str | Path) -> dict[str, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    compression = {"method": "gzip", "mtime": 0}
    keys = study["features"][["stock_code", "sector_code", "date"]].copy()
    label_output = keys.copy()
    label_output["retail_label"] = study["labels"].reset_index(drop=True)
    paths = {
        "labels": destination / "retail_resolved_labels.csv.gz",
        "summary": destination / "retail_study_summary.json",
    }
    label_output.to_csv(paths["labels"], index=False, compression=compression, encoding="utf-8")
    paths["summary"].write_text(
        json.dumps(_jsonable(study["summary"]), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT.parent / ".." / "business-rules" / "experiments" / "output" / "daily_ohlcv.csv.gz")
    parser.add_argument("--rules", type=Path, default=ROOT / "config" / "retail_rules_draft.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    parser.add_argument("--forward-days", type=int, default=5)
    parser.add_argument("--volume-window", type=int, default=20)
    parser.add_argument("--range-window", type=int, default=20)
    args = parser.parse_args()

    data = pd.read_csv(args.input, compression="infer", low_memory=False)
    rule_config = load_retail_rule_config(args.rules)
    study = run_retail_study(
        data,
        rule_config,
        forward_days=args.forward_days,
        volume_window=args.volume_window,
        range_window=args.range_window,
    )
    write_retail_outputs(study, args.output)
    summary = study["summary"]
    print(json.dumps(_jsonable(summary["coverage"]), ensure_ascii=False, indent=2))
    print("规则哈希:", summary["rules"]["sha256"])
    print("报告:", args.output / "retail_study_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
