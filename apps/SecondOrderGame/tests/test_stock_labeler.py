"""Regression tests for the frozen stock post-hoc labeler."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

from src.labeler.stock_labeler import StockLabeler, classify_participant
from src.labeler_constants import BEHAVIORS, PARTICIPANTS


CONFIG_PATH = Path(__file__).parent.parent / "config" / "labeler.yaml"
RESEARCH_DATA_PATH = Path(__file__).parent.parent / ".scratch" / "business-rules" / "experiments" / "output" / "daily_ohlcv.csv.gz"


def test_golden_sample_reproduces_manifest(golden_labeler_sample) -> None:
    labeler = StockLabeler(CONFIG_PATH)
    result = labeler.label_features(golden_labeler_sample["data"])
    manifest = golden_labeler_sample["manifest"]

    assert labeler.rule_hash == manifest["rule_hash"]
    assert result.counts == manifest["expected_counts"]
    assert all(result.counts[behavior] > 0 for behavior in BEHAVIORS)
    assert result.unlabeled_count == manifest["unlabeled_count"]
    assert result.unavailable_count == manifest["unavailable_count"]
    assert golden_labeler_sample["fixture_sha256"] == manifest["fixture_sha256"]


def test_full_research_ohlcv_uses_the_public_label_path(golden_labeler_sample) -> None:
    """The exact frozen distribution must pass through OHLCV feature engineering."""
    raw = pd.read_csv(RESEARCH_DATA_PATH, compression="gzip")
    labeler = StockLabeler(CONFIG_PATH)
    sectors = {
        code: frame
        for code, frame in raw.loc[raw["asset_type"].eq("sector")].groupby("code", sort=False)
    }
    counts = Counter()
    unlabeled = unavailable = multi_hit = 0
    for _, stock in raw.loc[raw["asset_type"].eq("stock")].groupby("code", sort=False):
        result = labeler.label(stock, sectors[stock["sector_code"].iloc[0]])
        counts.update(result.counts)
        unlabeled += result.unlabeled_count
        unavailable += result.unavailable_count
        multi_hit += result.multi_hit_count

    full = golden_labeler_sample["manifest"]["full_local_verification"]
    assert counts == full["expected_counts"]
    assert unlabeled == full["unlabeled_count"]
    assert unavailable == full["unavailable_count"]
    assert multi_hit == full["multi_hit_count"]


def test_golden_labeled_rows_have_one_known_participant_and_behavior(golden_labeler_sample) -> None:
    result = StockLabeler(CONFIG_PATH).label_features(golden_labeler_sample["data"])
    labels = result.rows.loc[result.rows["status"] == "labeled"]

    assert set(labels["participant"]) == set(PARTICIPANTS)
    assert set(labels["behavior"]) <= set(BEHAVIORS)
    assert labels["behavior"].notna().all()


def test_unmatched_rules_remain_unlabeled_not_watch() -> None:
    features = pd.DataFrame(
        [{
            "date": "2026-08-10", "benchmark_available": True,
            "forward_window_complete": True, "forward_stock_return": 0.01,
            "forward_sector_return": 0.01, "forward_excess_return": 0.0,
            "return_1d": 0.02, "volume_ratio_20": 2.0, "close_position": 0.5,
            "lower_shadow_ratio": 0.0, "upper_shadow_ratio": 0.0,
            "support_break_pct": 0.0, "resistance_break_pct": -0.03,
            "future_rebound_return": 0.0, "main_in_flow": 0.0,
            "super_in_flow": 0.0, "big_in_flow": 0.0,
        }]
    )

    result = StockLabeler(CONFIG_PATH).label_features(features)

    assert result.rows.loc[0, "status"] == "unlabeled"
    assert pd.isna(result.rows.loc[0, "behavior"])
    assert result.rows.loc[0, "reason"] == "no_rule_match"
    assert result.counts == Counter()


def test_missing_benchmark_is_unavailable_not_an_unmatched_rule() -> None:
    features = pd.DataFrame(
        [{
            "date": "2026-08-10", "benchmark_available": False,
            "forward_window_complete": True, "forward_stock_return": 0.10,
            "forward_sector_return": float("nan"), "forward_excess_return": float("nan"),
        }]
    )

    result = StockLabeler(CONFIG_PATH).label_features(features)

    assert result.rows.loc[0, "status"] == "unavailable"
    assert result.rows.loc[0, "reason"] == "missing_benchmark"
    assert result.unavailable_count == 1
    assert result.unlabeled_count == 0


def test_participant_classifier_uses_the_frozen_two_party_rule() -> None:
    assert classify_participant({"main_in_flow": 100, "super_in_flow": 40, "big_in_flow": 21}) == "主力"
    assert classify_participant({"main_in_flow": 100, "super_in_flow": 40, "big_in_flow": 20}) == "散户"
    assert classify_participant(None) == "散户"


def test_incomplete_forward_window_is_unavailable_even_with_finite_features() -> None:
    features = pd.DataFrame(
        [{
            "date": "2026-08-10", "benchmark_available": True,
            "forward_window_complete": False, "forward_stock_return": 0.10,
            "forward_sector_return": 0.01, "forward_excess_return": 0.09,
            "return_1d": 0.04, "volume_ratio_20": 1.5, "close_position": 0.9,
            "resistance_break_pct": 0.01,
        }]
    )

    result = StockLabeler(CONFIG_PATH).label_features(features)

    assert result.rows.loc[0, "status"] == "unavailable"
    assert result.rows.loc[0, "reason"] == "incomplete_forward_window"
    assert result.counts == Counter()


def test_fixed_priority_resolves_a_multi_hit_to_one_label() -> None:
    features = pd.DataFrame(
        [{
            "date": "2026-08-10", "benchmark_available": True,
            "forward_window_complete": True, "forward_stock_return": 0.04,
            "forward_sector_return": 0.01, "forward_excess_return": 0.03,
            "return_1d": -0.015, "volume_ratio_20": 1.20, "close_position": 0.50,
            "lower_shadow_ratio": 0.35, "upper_shadow_ratio": 0.0,
            "support_break_pct": -0.01, "resistance_break_pct": -0.03,
            "future_rebound_return": 0.03, "main_in_flow": 100.0,
            "super_in_flow": 40.0, "big_in_flow": 21.0,
        }]
    )

    result = StockLabeler(CONFIG_PATH).label_features(features)

    assert result.multi_hit_count == 1
    assert result.rows.loc[0, "behavior"] == "狩猎止损"
    assert result.rows.loc[0, "participant"] == "主力"
