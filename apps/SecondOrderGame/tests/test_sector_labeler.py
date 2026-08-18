"""Regression tests for the frozen sector-cycle post-hoc labeler."""

from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.config_validator import ConfigError
from src.labeler.sector_labeler import SectorLabeler
from src.labeler_constants import CYCLE_STATES


ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "sector_labeler.yaml"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "sector_labeler_sample.csv.gz"
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "sector_labeler_manifest.json"


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _feature_row(**changes: object) -> dict:
    row = {
        "date": "2026-08-10",
        "required_ohlcv_complete": True,
        "forward_window_complete": True,
        "zero_range": False,
        "return_1d": 0.0,
        "forward_return": 0.0,
        "volume_ratio_20": 1.0,
        "volatility_20": 0.01,
        "recent_trend_5d": 0.0,
        "consecutive_down_days": 0,
        "consecutive_shrink_days": 0,
        "price_position_20": 0.5,
    }
    row.update(changes)
    return row


def test_golden_ohlcv_sample_reproduces_manifest() -> None:
    labeler = SectorLabeler(CONFIG_PATH)
    fixture = pd.read_csv(FIXTURE_PATH, compression="gzip")
    manifest = _manifest()
    counts: Counter[str] = Counter()
    unlabeled = data_insufficient = multi_hit = 0

    for _, bars in fixture.groupby("sector_code", sort=True):
        result = labeler.label(bars)
        counts.update(result.counts)
        unlabeled += result.unlabeled_count
        data_insufficient += result.data_insufficient_count
        multi_hit += result.multi_hit_count

    assert labeler.rule_hash == manifest["rule_hash"]
    assert counts == manifest["expected_counts"]
    assert all(counts[state] > 0 for state in CYCLE_STATES)
    assert unlabeled == manifest["unlabeled_count"]
    assert data_insufficient == manifest["data_insufficient_count"]
    assert multi_hit == manifest["multi_hit_count"]
    assert hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest() == manifest["fixture_sha256"]


def test_labeled_rows_disclose_frozen_evidence_metadata() -> None:
    labeler = SectorLabeler(CONFIG_PATH)
    features = pd.DataFrame(
        [
            _feature_row(
                return_1d=0.01,
                forward_return=0.06,
                volume_ratio_20=1.0,
                recent_trend_5d=0.04,
                price_position_20=0.60,
            ),
            _feature_row(),
            _feature_row(required_ohlcv_complete=False),
        ]
    )

    result = labeler.label_features(features)

    fermentation = result.rows.iloc[0]
    assert fermentation["cycle_position"] == "发酵"
    assert fermentation["evidence_mode"] == "price_trend_proxy"
    assert fermentation["expansion_verified"] == False  # noqa: E712
    assert result.rows.loc[1:, "evidence_mode"].isna().all()
    assert result.rows.loc[1:, "expansion_verified"].isna().all()


def test_missing_ohlcv_column_returns_data_insufficient_without_a_label() -> None:
    bars = pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-01", periods=40),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
        }
    )

    result = SectorLabeler(CONFIG_PATH).label(bars)

    assert result.rows["status"].eq("data_insufficient").all()
    assert result.rows["cycle_position"].isna().all()
    assert result.rows["evidence_mode"].isna().all()
    assert result.rows["expansion_verified"].isna().all()


def test_lookback_and_forward_window_shortfalls_are_data_insufficient() -> None:
    bars = pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-01", periods=35),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000.0,
        }
    )

    result = SectorLabeler(CONFIG_PATH).label(bars)

    assert result.rows.iloc[0]["status"] == "data_insufficient"
    assert result.rows.iloc[-1]["status"] == "data_insufficient"
    assert result.rows.loc[result.rows["status"].eq("data_insufficient"), "cycle_position"].isna().all()


def test_unmatched_eligible_day_remains_unlabeled() -> None:
    result = SectorLabeler(CONFIG_PATH).label_features(pd.DataFrame([_feature_row()]))

    assert result.rows.loc[0, "status"] == "unlabeled"
    assert result.rows.loc[0, "reason"] == "no_rule_match"
    assert pd.isna(result.rows.loc[0, "cycle_position"])


def test_configured_priority_resolves_a_candidate_conflict() -> None:
    labeler = SectorLabeler(CONFIG_PATH)
    labeler._config["thresholds"]["启动"]["recent_trend_5d_max"] = 0.04
    features = pd.DataFrame(
        [
            _feature_row(
                return_1d=0.01,
                forward_return=0.06,
                volume_ratio_20=1.0,
                recent_trend_5d=0.04,
                price_position_20=0.60,
            )
        ]
    )

    result = labeler.label_features(features)

    assert result.multi_hit_count == 1
    assert result.rows.loc[0, "cycle_position"] == "发酵"


@pytest.mark.parametrize("forbidden", ["sentiment_index", "板块情绪指数"])
def test_sector_sentiment_input_is_rejected(forbidden: str) -> None:
    bars = pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-01", periods=40),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000.0,
            forbidden: 50.0,
        }
    )

    with pytest.raises(ValueError, match="sentiment inputs are forbidden"):
        SectorLabeler(CONFIG_PATH).label(bars)


def test_changed_rules_are_rejected_until_hash_and_version_are_refrozen(tmp_path: Path) -> None:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    changed = copy.deepcopy(config)
    changed["thresholds"]["高潮"]["return_1d_min"] = 0.031
    path = tmp_path / "sector_labeler.yaml"
    path.write_text(yaml.safe_dump(changed, allow_unicode=True, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="canonical configuration"):
        SectorLabeler(path)
