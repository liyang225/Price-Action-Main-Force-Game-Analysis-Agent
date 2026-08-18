"""Tests for the persistent post-hoc label ledger."""

from __future__ import annotations

from src.labeler.ledger import LabelLedger


def _sector_rows(*, rule_hash: str, version: int = 1):
    return [
        {
            "date": "2026-08-10",
            "cycle_position": "发酵",
            "status": "labeled",
            "reason": None,
            "evidence_mode": "price_trend_proxy",
            "expansion_verified": False,
            "rule_hash": rule_hash,
            "config_version": version,
        },
        {
            "date": "2026-08-11",
            "cycle_position": None,
            "status": "unlabeled",
            "reason": "no_rule_match",
            "evidence_mode": None,
            "expansion_verified": None,
            "rule_hash": rule_hash,
            "config_version": version,
        },
    ]


def _stock_rows(*, rule_hash: str, version: int = 1):
    return [
        {
            "date": "2026-08-10",
            "participant": "主力",
            "behavior": "建仓",
            "status": "labeled",
            "reason": None,
            "rule_hash": rule_hash,
            "config_version": version,
        },
        {
            "date": "2026-08-11",
            "participant": None,
            "behavior": None,
            "status": "unlabeled",
            "reason": "no_rule_match",
            "rule_hash": rule_hash,
            "config_version": version,
        },
    ]


def test_ledger_records_and_reads_sector_labels(tmp_path) -> None:
    with LabelLedger(tmp_path / "labels.db") as ledger:
        written = ledger.record_sector_labels("BK0475", _sector_rows(rule_hash="h1"))
        assert written == 2

        rows = ledger.sector_labels("BK0475", rule_hash="h1")
        assert len(rows) == 2
        assert rows[0].label == "发酵"
        assert rows[0].status == "labeled"
        assert rows[1].label is None
        assert rows[1].status == "unlabeled"


def test_ledger_upsert_is_idempotent(tmp_path) -> None:
    with LabelLedger(tmp_path / "labels.db") as ledger:
        ledger.record_sector_labels("BK0475", _sector_rows(rule_hash="h1"))
        # Re-recording the same rows must not duplicate them.
        ledger.record_sector_labels("BK0475", _sector_rows(rule_hash="h1"))
        assert len(ledger.sector_labels("BK0475", rule_hash="h1")) == 2


def test_ledger_isolates_rule_hashes(tmp_path) -> None:
    with LabelLedger(tmp_path / "labels.db") as ledger:
        ledger.record_sector_labels("BK0475", _sector_rows(rule_hash="old"))
        ledger.record_sector_labels("BK0475", _sector_rows(rule_hash="new"))
        assert len(ledger.sector_labels("BK0475", rule_hash="old")) == 2
        assert len(ledger.sector_labels("BK0475", rule_hash="new")) == 2


def test_ledger_records_stock_labels_and_counts(tmp_path) -> None:
    with LabelLedger(tmp_path / "labels.db") as ledger:
        ledger.record_stock_labels("SZ.000001", _stock_rows(rule_hash="h1"))
        rows = ledger.stock_labels("SZ.000001", rule_hash="h1")
        assert len(rows) == 2
        assert rows[0].label == "建仓"

        counts = ledger.label_counts("stock", rule_hash="h1")
        assert counts == {"建仓": 1}


def test_ledger_coverage_ratio(tmp_path) -> None:
    with LabelLedger(tmp_path / "labels.db") as ledger:
        ledger.record_sector_labels("BK0475", _sector_rows(rule_hash="h1"))
        coverage = ledger.coverage("sector", rule_hash="h1")
        assert coverage == 0.5  # one labeled of two eligible


def test_ledger_feature_json_persists(tmp_path) -> None:
    features = [{"date": "2026-08-10", "return_1d": 0.02, "volume_ratio_20": 1.5}]
    with LabelLedger(tmp_path / "labels.db") as ledger:
        ledger.record_sector_labels(
            "BK0475", _sector_rows(rule_hash="h1"), feature_rows=features
        )
        rows = ledger.sector_labels("BK0475", rule_hash="h1")
        assert rows[0].feature_json
        assert "volume_ratio_20" in rows[0].feature_json
