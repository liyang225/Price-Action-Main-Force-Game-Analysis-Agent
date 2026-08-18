"""Contract tests for the board-cycle v1/v2 configuration boundary."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from src.config_validator import (
    ConfigError,
    validate_dragon_tiger_inference_file,
    validate_sector_labeler,
    validate_sector_labeler_v2_file,
)


CONFIG_PATH = Path(__file__).parents[1] / "config" / "sector_labeler.yaml"


@pytest.fixture
def config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _set(config: dict, *path: str, value: object) -> dict:
    changed = copy.deepcopy(config)
    node = changed
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return changed


def _canonical_hash(config: dict) -> str:
    canonical = json.loads(json.dumps(config, ensure_ascii=False))
    canonical["rule_hash"]["frozen_hash"] = None
    payload = yaml.safe_dump(canonical, allow_unicode=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_current_sector_labeler_config_is_valid(config) -> None:
    validate_sector_labeler(config)


def test_v2_and_dragon_tiger_inference_configs_are_startup_validated() -> None:
    root = Path(__file__).parents[1]

    validate_sector_labeler_v2_file(root / "config" / "sector_labeler_v2.yaml")
    validate_dragon_tiger_inference_file(
        root / "config" / "dragon_tiger_inference.yaml"
    )


def test_state_metadata_must_cover_every_cycle_position(config) -> None:
    changed = copy.deepcopy(config)
    del changed["state_metadata"]["发酵"]

    with pytest.raises(ConfigError, match="缺少键"):
        validate_sector_labeler(changed)


def test_fermentation_must_disclose_price_proxy(config) -> None:
    changed = _set(
        config,
        "state_metadata",
        "发酵",
        "evidence_mode",
        value="ohlcv",
    )

    with pytest.raises(ConfigError, match="price_trend_proxy"):
        validate_sector_labeler(changed)


def test_v1_cannot_claim_expansion_was_verified(config) -> None:
    changed = _set(
        config,
        "state_metadata",
        "发酵",
        "expansion_verified",
        value=True,
    )

    with pytest.raises(ConfigError, match="expansion_verified"):
        validate_sector_labeler(changed)


def test_shadow_config_rejects_the_removed_completeness_rate(config) -> None:
    changed = _set(config, "shadow_v2", "required_completeness", value=0.95)

    with pytest.raises(ConfigError, match="shadow_v2"):
        validate_sector_labeler(changed)


def test_shadow_cutover_must_remain_automatic(config) -> None:
    changed = _set(config, "shadow_v2", "cutover_mode", value="manual")

    with pytest.raises(ConfigError, match="automatic cutover"):
        validate_sector_labeler(changed)


def test_frozen_hash_must_be_a_sha256_hex_digest(config) -> None:
    changed = _set(config, "rule_hash", "frozen_hash", value="bogus")

    with pytest.raises(ConfigError, match="64 lowercase hexadecimal"):
        validate_sector_labeler(changed)


def test_version_one_requires_the_canonical_rule_hash(config) -> None:
    changed = _set(config, "rule_hash", "frozen_hash", value="0" * 64)

    with pytest.raises(ConfigError, match="canonical configuration"):
        validate_sector_labeler(changed)


def test_version_zero_must_not_claim_a_frozen_hash(config) -> None:
    changed = _set(config, "version", value=0)
    changed["rule_hash"]["frozen_hash"] = _canonical_hash(changed)

    with pytest.raises(ConfigError, match="version 0"):
        validate_sector_labeler(changed)


def test_shadow_v2_uses_the_frozen_minimum_cutover_gates(config) -> None:
    assert config["shadow_v2"]["required_history_days_per_sector"] == 5
    assert config["shadow_v2"]["required_stable_trading_days"] == 3
