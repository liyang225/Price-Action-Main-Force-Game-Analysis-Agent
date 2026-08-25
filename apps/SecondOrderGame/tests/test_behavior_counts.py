"""Tests for the W behavior-matrix count backfill and posterior fusion."""

from __future__ import annotations

from src.labeler.behavior_counts import BehaviorCountStore
from src.labeler.ledger import LabelLedger


CYCLE_HASH = "sector-v1"
BEHAVIOR_HASH = "stock-v1"


PRIOR = {
    "冰点": {"主力": {"建仓": 0.49, "震仓": 0.11, "拉升": 0.05, "出货": 0.02, "观望": 0.29, "狩猎止损": 0.04}},
    "启动": {"主力": {"建仓": 0.26, "震仓": 0.18, "拉升": 0.34, "出货": 0.05, "观望": 0.15, "狩猎止损": 0.02}},
    "发酵": {"主力": {"建仓": 0.14, "震仓": 0.22, "拉升": 0.38, "出货": 0.10, "观望": 0.13, "狩猎止损": 0.03}},
    "高潮": {"主力": {"建仓": 0.02, "震仓": 0.06, "拉升": 0.21, "出货": 0.56, "观望": 0.13, "狩猎止损": 0.02}},
    "退潮": {"主力": {"建仓": 0.08, "震仓": 0.09, "拉升": 0.05, "出货": 0.34, "观望": 0.37, "狩猎止损": 0.07}},
}
ALPHA = {"冰点": {"主力": 11}, "启动": {"主力": 11}, "发酵": {"主力": 11}, "高潮": {"主力": 11}, "退潮": {"主力": 11}}


def _seed(ledger: LabelLedger) -> None:
    # A labeled sector day: 启动.
    ledger.record_sector_labels(
        "BK0475",
        [
            {
                "date": "2026-08-10",
                "cycle_position": "启动",
                "status": "labeled",
                "rule_hash": CYCLE_HASH,
                "config_version": 1,
            }
        ],
    )
    # A labeled stock day on the same date, main-force participant, in BK0475.
    ledger.record_stock_labels(
        "SZ.000001",
        [
            {
                "date": "2026-08-10",
                "participant": "主力",
                "behavior": "拉升",
                "status": "labeled",
                "rule_hash": BEHAVIOR_HASH,
                "config_version": 1,
            }
        ],
        feature_rows=[{"date": "2026-08-10", "sector_code": "BK0475"}],
        sector_code="BK0475",
    )


def test_reconcile_pairs_sector_cycle_with_stock_behavior(tmp_path) -> None:
    with LabelLedger(tmp_path / "labels.db") as ledger:
        _seed(ledger)
        with BehaviorCountStore(tmp_path / "behavior.db") as store:
            increments = store.reconcile(
                ledger,
                cycle_rule_hash=CYCLE_HASH,
                behavior_rule_hash=BEHAVIOR_HASH,
            )
    assert any("启动" in key and "拉升" in key for key in increments)


def test_reconcile_is_idempotent(tmp_path) -> None:
    with LabelLedger(tmp_path / "labels.db") as ledger:
        _seed(ledger)
        with BehaviorCountStore(tmp_path / "behavior.db") as store:
            first = store.reconcile(
                ledger, cycle_rule_hash=CYCLE_HASH, behavior_rule_hash=BEHAVIOR_HASH
            )
            second = store.reconcile(
                ledger, cycle_rule_hash=CYCLE_HASH, behavior_rule_hash=BEHAVIOR_HASH
            )
    assert second == {}
    assert first  # first pass did the counting


def test_reconcile_counts_main_force_label_that_arrives_after_sector_day(tmp_path) -> None:
    """A zero-match pass must not permanently close a sector/day."""
    with LabelLedger(tmp_path / "labels.db") as ledger:
        ledger.record_sector_labels(
            "BK0475",
            [
                {
                    "date": "2026-08-10",
                    "cycle_position": "启动",
                    "status": "labeled",
                    "rule_hash": CYCLE_HASH,
                    "config_version": 1,
                }
            ],
        )
        with BehaviorCountStore(tmp_path / "behavior.db") as store:
            assert store.reconcile(
                ledger,
                cycle_rule_hash=CYCLE_HASH,
                behavior_rule_hash=BEHAVIOR_HASH,
            ) == {}
            ledger.record_stock_labels(
                "SZ.000001",
                [
                    {
                        "date": "2026-08-10",
                        "participant": "主力",
                        "behavior": "拉升",
                        "status": "labeled",
                        "rule_hash": BEHAVIOR_HASH,
                        "config_version": 1,
                    }
                ],
                sector_code="BK0475",
            )
            increments = store.reconcile(
                ledger,
                cycle_rule_hash=CYCLE_HASH,
                behavior_rule_hash=BEHAVIOR_HASH,
            )

    assert any("启动" in key and "拉升" in key for key in increments)


def test_reconcile_skips_retail_participant(tmp_path) -> None:
    with LabelLedger(tmp_path / "labels.db") as ledger:
        _seed(ledger)
        # Overwrite with a retail-participant row: must NOT count into W.
        ledger.record_stock_labels(
            "SZ.000001",
            [
                {
                    "date": "2026-08-10",
                    "participant": "散户",
                    "behavior": "拉升",
                    "status": "labeled",
                    "rule_hash": BEHAVIOR_HASH,
                    "config_version": 1,
                }
            ],
            feature_rows=[{"date": "2026-08-10", "sector_code": "BK0475"}],
            sector_code="BK0475",
        )
        with BehaviorCountStore(tmp_path / "behavior.db") as store:
            increments = store.reconcile(
                ledger, cycle_rule_hash=CYCLE_HASH, behavior_rule_hash=BEHAVIOR_HASH
            )
    assert increments == {}


def test_posterior_fuses_prior_and_counts(tmp_path) -> None:
    with LabelLedger(tmp_path / "labels.db") as ledger:
        _seed(ledger)
        with BehaviorCountStore(tmp_path / "behavior.db") as store:
            store.reconcile(
                ledger, cycle_rule_hash=CYCLE_HASH, behavior_rule_hash=BEHAVIOR_HASH
            )
            posterior = store.posterior(
                cycle_rule_hash=CYCLE_HASH,
                behavior_rule_hash=BEHAVIOR_HASH,
                prior=PRIOR,
                alpha=ALPHA,
            )
    # 启动/主力 row normalized and 拉升 pulled above its prior.
    row = posterior["启动"]["主力"]
    assert abs(sum(row.values()) - 1.0) < 1e-9
    assert row["拉升"] > PRIOR["启动"]["主力"]["拉升"]


def test_posterior_empty_store_returns_prior(tmp_path) -> None:
    with BehaviorCountStore(tmp_path / "behavior.db") as store:
        posterior = store.posterior(
            cycle_rule_hash=CYCLE_HASH,
            behavior_rule_hash=BEHAVIOR_HASH,
            prior=PRIOR,
            alpha=ALPHA,
        )
    # No counts: posterior equals the normalized prior.
    for cycle, participants in PRIOR.items():
        for participant, row in participants.items():
            total = sum(row.values())
            expected = {k: v / total for k, v in row.items()}
            for behavior, value in expected.items():
                assert abs(posterior[cycle][participant][behavior] - value) < 1e-9
