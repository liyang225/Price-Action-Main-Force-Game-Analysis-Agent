from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from behavior_study.pipeline import run_study, write_study_outputs
from behavior_study.rules import load_rule_config


def _bars(code: str, kind: str, closes: list[float], primary_plate: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": code,
            "instrument_type": kind,
            "primary_plate_code": primary_plate,
            "date": pd.date_range("2024-01-01", periods=len(closes), freq="D"),
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [100, 100, 120, 90, 130, 100, 110, 95],
        }
    )


def test_pipeline_joins_each_stock_to_its_primary_sector_and_writes_artifacts(tmp_path) -> None:
    data = pd.concat(
        [
            _bars("SH.600001", "stock", [10, 9.5, 9.8, 10.2, 10.4, 10.8, 11.0, 11.2], "SH.LIST0002"),
            _bars("SH.LIST0002", "sector", [20, 19.8, 20.0, 20.2, 20.3, 20.5, 20.6, 20.7], "SH.LIST0002"),
        ],
        ignore_index=True,
    )
    rules = load_rule_config(Path(__file__).resolve().parents[1] / "config" / "behavior_rules.yaml")

    study = run_study(data, rules, forward_days=2, volume_window=2)

    assert len(study["features"]) == 8
    assert study["features"]["sector_code"].eq("SH.LIST0002").all()
    assert study["features"]["market_forward_return"].isna().all()
    assert study["summary"]["extreme_market"]["valid_count"] == 0
    assert study["summary"]["unmatched_analysis"]["coverage_by_sector"]["SH.LIST0002"]["eligible_count"] == 6
    assert tuple(study["masks"].columns) == tuple(rules.rules.keys())
    paths = write_study_outputs(study, tmp_path)
    assert paths["features"].exists()
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert summary["input"]["stock_count"] == 1
    assert summary["overlap"]["row_count"] == 6
    assert summary["unmatched_analysis"]["eligible_row_count"] == 6
