"""Tests for C confusion-count backfill and the LLM observation sink."""

from __future__ import annotations

from pathlib import Path

from src.labeler.confusion_counts import (
    ConfusionCountStore,
    build_llm_observation_sink,
)
from src.labeler.ledger import LabelLedger


PRIOR = {
    "冰点": {"冰点": 0.55, "启动": 0.25, "发酵": 0.10, "高潮": 0.02, "退潮": 0.08},
    "启动": {"冰点": 0.15, "启动": 0.55, "发酵": 0.20, "高潮": 0.05, "退潮": 0.05},
    "发酵": {"冰点": 0.05, "启动": 0.20, "发酵": 0.50, "高潮": 0.15, "退潮": 0.10},
    "高潮": {"冰点": 0.02, "启动": 0.05, "发酵": 0.18, "高潮": 0.60, "退潮": 0.15},
    "退潮": {"冰点": 0.10, "启动": 0.03, "发酵": 0.12, "高潮": 0.20, "退潮": 0.55},
}
ALPHA = {"冰点": 5, "启动": 4, "发酵": 4, "高潮": 5, "退潮": 5}


def _seed_ledger(ledger: LabelLedger, *, rule_hash: str) -> None:
    ledger.record_sector_labels(
        "BK0475",
        [
            {
                "date": "2026-08-10",
                "cycle_position": "发酵",
                "status": "labeled",
                "reason": None,
                "rule_hash": rule_hash,
                "config_version": 1,
            }
        ],
    )


def test_record_and_reconcile_llm_observation(tmp_path) -> None:
    rule_hash = "sector-v1"
    with LabelLedger(tmp_path / "labels.db") as ledger:
        _seed_ledger(ledger, rule_hash=rule_hash)
        with ConfusionCountStore(tmp_path / "confusion.db") as store:
            store.record_llm_observation("BK0475", "2026-08-10", "发酵")
            increments = store.reconcile(ledger, rule_hash=rule_hash)
            assert increments == {("sector-v1", "发酵", "发酵"): 1}

            # Reconciled observations must not double count.
            assert store.reconcile(ledger, rule_hash=rule_hash) == {}
            counts = store.counts(rule_hash=rule_hash)
            assert counts == {("发酵", "发酵"): 1}


def test_observation_without_true_label_is_not_counted(tmp_path) -> None:
    rule_hash = "sector-v1"
    with LabelLedger(tmp_path / "labels.db") as ledger:
        # Ledger has an unlabeled row only; no true label to pair.
        ledger.record_sector_labels(
            "BK0475",
            [
                {
                    "date": "2026-08-10",
                    "cycle_position": None,
                    "status": "unlabeled",
                    "reason": "no_rule_match",
                    "rule_hash": rule_hash,
                    "config_version": 1,
                }
            ],
        )
        with ConfusionCountStore(tmp_path / "confusion.db") as store:
            store.record_llm_observation("BK0475", "2026-08-10", "发酵")
            assert store.reconcile(ledger, rule_hash=rule_hash) == {}


def test_posterior_fuses_prior_and_counts(tmp_path) -> None:
    rule_hash = "sector-v1"
    with LabelLedger(tmp_path / "labels.db") as ledger:
        _seed_ledger(ledger, rule_hash=rule_hash)
        with ConfusionCountStore(tmp_path / "confusion.db") as store:
            store.record_llm_observation("BK0475", "2026-08-10", "发酵")
            store.reconcile(ledger, rule_hash=rule_hash)
            posterior = store.posterior(rule_hash=rule_hash, prior=PRIOR, alpha=ALPHA)
            # Every row normalized to 1.0.
            for row in posterior.values():
                assert abs(sum(row.values()) - 1.0) < 1e-9
            # The observed (发酵, 发酵) cell must be pulled above its prior.
            assert posterior["发酵"]["发酵"] > PRIOR["发酵"]["发酵"]


def test_build_llm_observation_sink_records_unknown_ignored(tmp_path) -> None:
    sink = build_llm_observation_sink(tmp_path / "confusion.db")
    sink("BK0475", "2026-08-10", "发酵")
    sink("unknown", "2026-08-10", "发酵")  # must be ignored without raising
    with ConfusionCountStore(tmp_path / "confusion.db") as store:
        observations = store.unreconciled_observations()
        assert len(observations) == 1
        assert observations[0]["sector_code"] == "BK0475"
