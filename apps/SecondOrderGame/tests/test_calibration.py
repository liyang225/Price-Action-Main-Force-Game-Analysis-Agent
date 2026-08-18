"""Tests for the calibrated HMM config loader (prior + counts fusion)."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.labeler.behavior_counts import BehaviorCountStore
from src.labeler.calibration import (
    load_calibrated_hmm_config,
    load_calibrated_hmm_config_from_files,
)
from src.labeler.confusion_counts import ConfusionCountStore
from src.labeler.ledger import LabelLedger
from src.labeler.sector_labeler import SectorLabeler
from src.labeler.stock_labeler import StockLabeler


ROOT = Path(__file__).parent.parent
HMM_CONFIG = ROOT / "config" / "hmm_prior.yaml"


def _hmm_config() -> dict:
    return yaml.safe_load(HMM_CONFIG.read_text(encoding="utf-8"))


def test_calibrated_config_returns_prior_when_stores_empty(tmp_path) -> None:
    prior = _hmm_config()
    confusion = ConfusionCountStore(tmp_path / "confusion.db")
    behavior = BehaviorCountStore(tmp_path / "behavior.db")
    try:
        fused = load_calibrated_hmm_config(
            prior,
            confusion_store=confusion,
            behavior_store=behavior,
            cycle_rule_hash="nope",
            behavior_rule_hash="nope",
        )
    finally:
        confusion.close()
        behavior.close()
    # All three matrices still present.
    assert set(fused) >= {"confusion_matrix", "behavior_mapping", "transition_matrix"}
    # C prior rows preserved (normalized floats).
    for true_state in ("冰点", "启动", "发酵", "高潮", "退潮"):
        row = fused["confusion_matrix"][f"true_{true_state}"]
        probs = {k: v for k, v in row.items() if k != "alpha"}
        assert abs(sum(probs.values()) - 1.0) < 1e-9
    # W rows preserved.
    for participant in ("主力", "散户"):
        row = fused["behavior_mapping"]["高潮"][participant]
        probs = {k: v for k, v in row.items() if k != "alpha"}
        assert abs(sum(probs.values()) - 1.0) < 1e-9


def test_calibrated_config_pulls_behavior_posterior(tmp_path) -> None:
    prior = _hmm_config()
    sector_rule = SectorLabeler().rule_hash
    stock_rule = StockLabeler().rule_hash
    with LabelLedger(tmp_path / "labels.db") as ledger:
        ledger.record_sector_labels(
            "BK0475",
            [
                {
                    "date": "2026-08-10",
                    "cycle_position": "高潮",
                    "status": "labeled",
                    "rule_hash": sector_rule,
                    "config_version": 1,
                }
            ],
        )
        ledger.record_stock_labels(
            "SZ.000001",
            [
                {
                    "date": "2026-08-10",
                    "participant": "主力",
                    "behavior": "拉升",
                    "status": "labeled",
                    "rule_hash": stock_rule,
                    "config_version": 1,
                }
            ],
            feature_rows=[{"date": "2026-08-10", "sector_code": "BK0475"}],
            sector_code="BK0475",
        )
        behavior = BehaviorCountStore(tmp_path / "behavior.db")
        behavior.reconcile(
            ledger, cycle_rule_hash=sector_rule, behavior_rule_hash=stock_rule
        )
        behavior.close()

    confusion = ConfusionCountStore(tmp_path / "confusion.db")
    behavior = BehaviorCountStore(tmp_path / "behavior.db")
    try:
        fused = load_calibrated_hmm_config(
            prior,
            confusion_store=confusion,
            behavior_store=behavior,
            cycle_rule_hash=sector_rule,
            behavior_rule_hash=stock_rule,
        )
    finally:
        confusion.close()
        behavior.close()
    # 高潮/主力 拉升 pulled above its hand-written prior.
    prior_row = prior["behavior_mapping"]["高潮"]["主力"]
    prior_total = sum(v for k, v in prior_row.items() if k != "alpha")
    prior_lash = prior_row["拉升"] / prior_total
    assert fused["behavior_mapping"]["高潮"]["主力"]["拉升"] > prior_lash


def test_from_files_roundtrip(tmp_path) -> None:
    fused = load_calibrated_hmm_config_from_files(
        HMM_CONFIG,
        confusion_database=tmp_path / "confusion.db",
        behavior_database=tmp_path / "behavior.db",
        cycle_rule_hash=SectorLabeler().rule_hash,
        behavior_rule_hash=StockLabeler().rule_hash,
    )
    assert "behavior_mapping" in fused
    assert "confusion_matrix" in fused
