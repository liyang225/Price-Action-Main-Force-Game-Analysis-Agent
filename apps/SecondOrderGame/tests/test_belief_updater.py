from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.data.sentiment_ledger import SentimentLedger
from src.hmm_filter import HMMFilter, load_config
from src.reasoning.belief_updater import (
    BeliefUpdater,
    BeliefUpdaterError,
    K120MCloseEvent,
)


BASE_TIME = datetime(2026, 8, 10, 3, 30, tzinfo=timezone.utc)


@pytest.fixture
def real_config() -> dict:
    return load_config(Path(__file__).parent.parent / "config" / "hmm_prior.yaml")


def _event(
    sector_code: str,
    state: str,
    offset: int = 0,
    *,
    complete: bool = True,
    interval: str = "K_120M",
) -> K120MCloseEvent:
    return K120MCloseEvent(
        sector_code=sector_code,
        closed_at=BASE_TIME + timedelta(hours=offset),
        observed_cycle_state=state,
        is_complete=complete,
        interval=interval,
    )


def test_each_sector_has_an_independent_filter_and_checkpoint(real_config, tmp_path) -> None:
    ledger = SentimentLedger(tmp_path / "belief.sqlite")
    updater = BeliefUpdater(real_config, ledger)

    semiconductor = updater.update(_event("BK001", "高潮"))
    bank = updater.update(_event("BK002", "冰点"))

    assert semiconductor is not None
    assert bank is not None
    assert semiconductor.belief != bank.belief
    assert updater.belief_for("BK001") == semiconductor.belief
    assert updater.belief_for("BK002") == bank.belief
    assert ledger.load_belief("BK001").belief == semiconductor.belief
    assert ledger.load_belief("BK002").belief == bank.belief


def test_restart_restores_belief_before_advancing_the_next_bar(real_config, tmp_path) -> None:
    database = tmp_path / "belief.sqlite"
    with SentimentLedger(database) as ledger:
        first_process = BeliefUpdater(real_config, ledger)
        first = first_process.update(_event("BK001", "启动"))

    with SentimentLedger(database) as ledger:
        restarted = BeliefUpdater(real_config, ledger)
        assert restarted.belief_for("BK001") == first.belief
        second = restarted.update(_event("BK001", "发酵", 2))

    expected = HMMFilter(real_config, "BK001")
    expected.restore_belief(first.belief)
    assert second.belief == expected.update("发酵")


def test_duplicate_closed_bar_is_idempotent_after_restart(real_config, tmp_path) -> None:
    database = tmp_path / "belief.sqlite"
    event = _event("BK001", "启动")
    with SentimentLedger(database) as ledger:
        first = BeliefUpdater(real_config, ledger).update(event)

    with SentimentLedger(database) as ledger:
        restarted = BeliefUpdater(real_config, ledger)
        assert restarted.update(event) is None
        assert restarted.belief_for("BK001") == first.belief


def test_incomplete_bar_does_not_update_or_create_a_checkpoint(real_config, tmp_path) -> None:
    ledger = SentimentLedger(tmp_path / "belief.sqlite")
    updater = BeliefUpdater(real_config, ledger)

    assert updater.update(_event("BK001", "启动", complete=False)) is None
    assert updater.belief_for("BK001") is None
    assert ledger.load_belief("BK001") is None


def test_non_k120m_close_is_rejected_without_updating(real_config, tmp_path) -> None:
    ledger = SentimentLedger(tmp_path / "belief.sqlite")
    updater = BeliefUpdater(real_config, ledger)

    with pytest.raises(ValueError, match="K_120M"):
        updater.update(_event("BK001", "启动", interval="K_240M"))

    assert updater.belief_for("BK001") is None


def test_unknown_cycle_state_is_rejected_without_updating(real_config, tmp_path) -> None:
    ledger = SentimentLedger(tmp_path / "belief.sqlite")
    updater = BeliefUpdater(real_config, ledger)

    with pytest.raises(ValueError, match="observed_cycle_state"):
        updater.update(_event("BK001", "分歧"))

    assert updater.belief_for("BK001") is None


def test_out_of_order_bar_is_rejected_without_replaying_history(real_config, tmp_path) -> None:
    ledger = SentimentLedger(tmp_path / "belief.sqlite")
    updater = BeliefUpdater(real_config, ledger)
    updater.update(_event("BK001", "启动", 2))

    with pytest.raises(BeliefUpdaterError, match="out-of-order"):
        updater.update(_event("BK001", "发酵", 0))


def test_checkpoint_from_a_different_hmm_config_version_is_rejected(real_config, tmp_path) -> None:
    ledger = SentimentLedger(tmp_path / "belief.sqlite")
    BeliefUpdater(real_config, ledger).update(_event("BK001", "启动"))
    next_version = dict(real_config)
    next_version["version"] = real_config["version"] + 1

    with pytest.raises(BeliefUpdaterError, match="current config"):
        BeliefUpdater(next_version, ledger).update(_event("BK001", "发酵", 2))
