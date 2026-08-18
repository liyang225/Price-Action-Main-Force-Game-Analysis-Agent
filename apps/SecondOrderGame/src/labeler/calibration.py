"""Fuse hand-written HMM priors with accumulated post-hoc label counts.

The HMM filter (``src/hmm_filter.py``) reads ``hmm_prior.yaml`` verbatim.  This
module produces a *calibrated* config dict by fusing those priors with the
counts accumulated by the C confusion store and the W behavior store:

- ``confusion_matrix``  → ``ConfusionCountStore.posterior``
- ``behavior_mapping``  → ``BehaviorCountStore.posterior``

When a store is empty or absent the original prior rows pass through unchanged,
so a fresh system behaves exactly like the un-calibrated HMM (zero regression).

The caller must provide the stores; opening them per decision is cheap because
each reconcile/backfill step is idempotent and the reads are single SQL scans.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from src.labeler.behavior_counts import BehaviorCountStore
from src.labeler.confusion_counts import ConfusionCountStore


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HMM_CONFIG = ROOT / "config" / "hmm_prior.yaml"
DEFAULT_LEDGER = ROOT / "runtime" / "labeler" / "labels.db"
DEFAULT_CONFUSION = ROOT / "runtime" / "labeler" / "confusion.db"
DEFAULT_BEHAVIOR = ROOT / "runtime" / "labeler" / "behavior.db"


def _confusion_prior(config: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    return {
        key.removeprefix("true_"): {
            inner.removeprefix("llm_"): float(value)
            for inner, value in row.items()
            if inner != "alpha"
        }
        for key, row in config["confusion_matrix"].items()
    }


def _confusion_alpha(config: Mapping[str, Any]) -> dict[str, float]:
    return {
        key.removeprefix("true_"): float(row["alpha"])
        for key, row in config["confusion_matrix"].items()
    }


def _behavior_prior(config: Mapping[str, Any]) -> dict[str, dict[str, dict[str, float]]]:
    return {
        cycle: {
            participant: {
                behavior: float(value)
                for behavior, value in row.items()
                if behavior != "alpha"
            }
            for participant, row in participants.items()
            if participant != "alpha"
        }
        for cycle, participants in config["behavior_mapping"].items()
    }


def _behavior_alpha(config: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    return {
        cycle: {
            participant: float(row["alpha"])
            for participant, row in participants.items()
            if participant != "alpha" and "alpha" in row
        }
        for cycle, participants in config["behavior_mapping"].items()
    }


def load_calibrated_hmm_config(
    hmm_config: Mapping[str, Any],
    *,
    confusion_store: ConfusionCountStore | None,
    behavior_store: BehaviorCountStore | None,
    cycle_rule_hash: str,
    behavior_rule_hash: str,
) -> dict[str, Any]:
    """Return a deep copy of ``hmm_config`` with C/W fused to posteriors.

    ``cycle_rule_hash`` identifies the sector labeler whose labels produced the
    C counts; ``behavior_rule_hash`` identifies the stock labeler whose labels
    produced the W counts.  Prior rows are replaced by fused rows (still
    carrying ``alpha`` so ``prior_weight`` can be reported downstream).
    """
    result = _deep_copy_config(hmm_config)

    if confusion_store is not None:
        posterior_c = confusion_store.posterior(
            rule_hash=cycle_rule_hash,
            prior=_confusion_prior(hmm_config),
            alpha=_confusion_alpha(hmm_config),
        )
        result["confusion_matrix"] = {
            f"true_{true_state}": {
                **{f"llm_{llm}": float(value) for llm, value in row.items()},
                "alpha": _confusion_alpha(hmm_config).get(true_state, 1.0),
            }
            for true_state, row in posterior_c.items()
        }

    if behavior_store is not None:
        posterior_w = behavior_store.posterior(
            cycle_rule_hash=cycle_rule_hash,
            behavior_rule_hash=behavior_rule_hash,
            prior=_behavior_prior(hmm_config),
            alpha=_behavior_alpha(hmm_config),
        )
        alpha_w = _behavior_alpha(hmm_config)
        result["behavior_mapping"] = {
            cycle: {
                participant: {
                    **{behavior: float(value) for behavior, value in row.items()},
                    "alpha": alpha_w.get(cycle, {}).get(participant, 1.0),
                }
                for participant, row in participants.items()
            }
            for cycle, participants in posterior_w.items()
        }

    return result


def load_calibrated_hmm_config_from_files(
    hmm_config_path: Path | str = DEFAULT_HMM_CONFIG,
    *,
    confusion_database: Path | str | None,
    behavior_database: Path | str | None,
    cycle_rule_hash: str,
    behavior_rule_hash: str,
) -> dict[str, Any]:
    """Convenience loader: read the YAML and open the stores, then fuse."""
    config = yaml.safe_load(Path(hmm_config_path).read_text(encoding="utf-8"))
    confusion_store = ConfusionCountStore(confusion_database) if confusion_database else None
    behavior_store = BehaviorCountStore(behavior_database) if behavior_database else None
    try:
        return load_calibrated_hmm_config(
            config,
            confusion_store=confusion_store,
            behavior_store=behavior_store,
            cycle_rule_hash=cycle_rule_hash,
            behavior_rule_hash=behavior_rule_hash,
        )
    finally:
        if confusion_store is not None:
            confusion_store.close()
        if behavior_store is not None:
            behavior_store.close()


def load_production_hmm_config(
    *,
    hmm_config_path: Path | str = DEFAULT_HMM_CONFIG,
    confusion_database: Path | str | None = DEFAULT_CONFUSION,
    behavior_database: Path | str | None = DEFAULT_BEHAVIOR,
) -> dict[str, Any]:
    """Load the production HMM config fused with accumulated label counts.

    Derives the rule hashes from the frozen labeler configs (sector + stock).
    A fresh system (empty count stores) returns the hand-written prior
    unchanged, so production behavior is identical until the labelers have
    accumulated evidence.
    """
    from src.labeler.sector_labeler import SectorLabeler
    from src.labeler.stock_labeler import StockLabeler

    cycle_rule_hash = SectorLabeler().rule_hash
    behavior_rule_hash = StockLabeler().rule_hash
    return load_calibrated_hmm_config_from_files(
        hmm_config_path,
        confusion_database=confusion_database,
        behavior_database=behavior_database,
        cycle_rule_hash=cycle_rule_hash,
        behavior_rule_hash=behavior_rule_hash,
    )


def _deep_copy_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return yaml.safe_load(
        yaml.safe_dump(dict(config), allow_unicode=True, sort_keys=False)
    )


__all__ = [
    "load_calibrated_hmm_config",
    "load_calibrated_hmm_config_from_files",
    "load_production_hmm_config",
]
