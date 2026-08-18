"""Validated, side-effect-free prompt routing for the reasoning pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml

try:  # Keep the repository's shared top-level module identity when available.
    from config_validator import CYCLE_STATES, ConfigError, PARTICIPANTS, validate_prompt_routing
except ModuleNotFoundError:  # pragma: no cover - exercised by installed-package hosts
    from src.config_validator import (
        CYCLE_STATES,
        ConfigError,
        PARTICIPANTS,
        validate_prompt_routing,
    )


RouteTable = Mapping[str, Mapping[str, Path]]


@dataclass(frozen=True)
class PromptRouter:
    """An immutable table of prompt paths validated when the router is loaded."""

    _routes: RouteTable
    _common: Mapping[str, Path]
    _registered_paths: tuple[Path, ...]
    config_version: int
    _user_experience_path: Path | None = None

    @classmethod
    def from_config(cls, config: dict, prompt_root: Path | str) -> "PromptRouter":
        """Build a router after validating every configured prompt path once."""
        root = Path(prompt_root).resolve()
        validate_prompt_routing(config, root)

        routes = {
            cycle_state: MappingProxyType(
                {
                    participant: (root / config["routes"][cycle_state][participant]).resolve()
                    for participant in PARTICIPANTS
                }
            )
            for cycle_state in CYCLE_STATES
        }
        common = MappingProxyType(
            {
                name: (root / raw_path).resolve()
                for name, raw_path in config.get("common", {}).items()
            }
        )
        registered_paths = tuple(
            (root / raw_path).resolve() for raw_path in config.get("registry", [])
        )
        user_experience_path = next(
            (path for path in registered_paths if path.name == "用户经验.txt"),
            None,
        )
        return cls(
            MappingProxyType(routes),
            common,
            registered_paths,
            int(config["version"]),
            user_experience_path,
        )

    def route(self, cycle_state: str, participant: str) -> Path:
        """Return the pre-validated route for a known cycle state and participant."""
        if cycle_state not in CYCLE_STATES:
            raise ValueError(f"未知情绪周期位置：{cycle_state!r}")
        if participant not in PARTICIPANTS:
            raise ValueError(f"未知参与者：{participant!r}")
        return self._routes[cycle_state][participant]

    def common(self, name: str) -> Path:
        """Return a registered common prompt without exposing arbitrary paths."""
        try:
            return self._common[name]
        except KeyError as exc:
            raise ValueError(f"未知通用提示词：{name!r}") from exc

    def with_user_experience(self, base_prompt: str) -> str:
        """Append the optional user-experience note to a system prompt.

        The note lives in ``prompt_engine/通用/用户经验.txt`` and is injected
        into the reasoning steps the user opted into.  A missing or empty note
        is a no-op, so the default (empty) file does not change any prompt.
        """
        if self._user_experience_path is None:
            return base_prompt
        try:
            experience = self._user_experience_path.read_text(encoding="utf-8").strip()
        except OSError:
            return base_prompt
        if not experience:
            return base_prompt
        return f"{base_prompt}\n\n## 用户经验（请优先遵循）\n{experience}"

    @property
    def registered_paths(self) -> tuple[Path, ...]:
        return self._registered_paths


def load_prompt_router(
    routing_config_path: Path | str,
    prompt_root: Path | str,
) -> PromptRouter:
    """Load and validate a routing configuration before creating a pure router."""
    source = Path(routing_config_path)
    try:
        with source.open(encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"[{source.resolve()}] cannot load YAML: {error}") from error
    if not isinstance(config, dict):
        raise ConfigError(f"[{source.name}] 顶层结构应为映射")
    return PromptRouter.from_config(config, prompt_root)
