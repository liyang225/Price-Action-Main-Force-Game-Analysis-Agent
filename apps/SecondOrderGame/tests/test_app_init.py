"""Startup configuration validation tests."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from src.app_init import initialize_application
from src.config_validator import ConfigError


CONFIG_DIR = Path(__file__).parent.parent / "config"
ENABLED_CONFIG_NAMES = (
    "hmm_prior.yaml",
    "signals.yaml",
    "labeler.yaml",
    "sectors.yaml",
    "sentiment.yaml",
)


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as config_file:
        return yaml.safe_load(config_file)


def test_initialize_application_accepts_the_enabled_configuration_set():
    initialize_application(CONFIG_DIR)


def test_initialize_application_reuses_the_unified_validation_entrypoint(monkeypatch):
    calls = []

    def validate_all(config_dir):
        calls.append(config_dir)

    monkeypatch.setattr("src.app_init.validate_all", validate_all)

    initialize_application(CONFIG_DIR)

    assert calls == [CONFIG_DIR]


def test_initialize_application_fails_with_config_source_and_constraint(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for source_name in ENABLED_CONFIG_NAMES:
        source = CONFIG_DIR / source_name
        config = _load(source)
        if source.name == "signals.yaml":
            config = copy.deepcopy(config)
            config["nash"]["deviation"] = 0.02
        with open(config_dir / source.name, "w", encoding="utf-8") as destination:
            yaml.safe_dump(config, destination, allow_unicode=True, sort_keys=False)

    with pytest.raises(ConfigError) as exc_info:
        initialize_application(config_dir)

    message = str(exc_info.value)
    assert "signals.yaml" in message
    assert "signals.nash.deviation" in message


def test_initialize_application_reports_malformed_yaml_with_its_source(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for source_name in ENABLED_CONFIG_NAMES:
        source = CONFIG_DIR / source_name
        destination = config_dir / source.name
        if source.name == "sentiment.yaml":
            destination.write_text("range: [", encoding="utf-8")
        else:
            destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ConfigError, match="sentiment.yaml") as exc_info:
        initialize_application(config_dir)

    assert "cannot load YAML" in str(exc_info.value)
