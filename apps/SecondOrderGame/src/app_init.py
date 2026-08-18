"""Application startup boundary."""

from __future__ import annotations

from pathlib import Path

from src.config_validator import validate_all


def initialize_application(config_dir: Path | str | None = None) -> None:
    """Fail fast when any enabled configuration is invalid."""
    validate_all(config_dir)
