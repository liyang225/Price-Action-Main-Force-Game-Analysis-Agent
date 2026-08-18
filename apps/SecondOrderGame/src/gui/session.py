"""Unsaved, validated editing session for ``hmm_prior.yaml``."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping

import yaml

from src.config_validator import ConfigError, validate


_DEFAULT_GUI_CONFIG = Path(__file__).parents[2] / "config" / "gui.yaml"


def load_confidence_alpha(path: Path | str = _DEFAULT_GUI_CONFIG) -> dict[str, float]:
    source = Path(path)
    loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
    try:
        values = loaded["confidence_alpha"]
        result = {label: float(values[label]) for label in ("弱", "中", "强")}
    except (KeyError, TypeError, ValueError) as error:
        raise ConfigError(f"[{source}] confidence_alpha 必须完整定义弱/中/强") from error
    if any(not math.isfinite(value) or value < 0.3 for value in result.values()):
        raise ConfigError(f"[{source}] confidence_alpha 必须是至少 0.3 的有限数字")
    return result


@dataclass(frozen=True, slots=True)
class RowRef:
    """Stable public address of one editable probability row."""

    section: str
    row: str
    participant: str | None = None


class ConfigSession:
    """Own a draft independently from the file and its last valid state."""

    def __init__(self, config: Mapping[str, Any], source_path: Path | str | None = None):
        copied = deepcopy(dict(config))
        validate(copied)
        self.source_path = Path(source_path).resolve() if source_path is not None else None
        self._original = deepcopy(copied)
        self._draft = deepcopy(copied)
        self._last_valid = deepcopy(copied)
        self._validation_error: str | None = None

    @classmethod
    def from_file(cls, path: Path | str) -> "ConfigSession":
        source = Path(path)
        with source.open(encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
        if not isinstance(config, dict):
            raise ConfigError(f"[{source}] 顶层结构应为映射")
        return cls(config, source)

    @property
    def config(self) -> dict[str, Any]:
        return deepcopy(self._draft)

    @property
    def last_valid_config(self) -> dict[str, Any]:
        return deepcopy(self._last_valid)

    @property
    def base_version(self) -> int:
        return int(self._original["version"])

    @property
    def is_dirty(self) -> bool:
        return self._draft != self._original

    @property
    def is_valid(self) -> bool:
        return self._validation_error is None

    @property
    def validation_error(self) -> str | None:
        return self._validation_error

    def row(self, ref: RowRef) -> dict[str, Any]:
        return deepcopy(self._resolve_row(self._draft, ref))

    def set_beginner_percentage(
        self, ref: RowRef, key: str, percentage: float
    ) -> dict[str, Any]:
        """Set one percentage and redistribute the remainder across its peers."""
        numeric = self._finite_number(percentage, "百分比")
        if numeric < 0 or numeric > 100:
            raise ValueError("百分比必须位于 0 到 100 之间")

        candidate = deepcopy(self._draft)
        row = self._resolve_row(candidate, ref)
        probability_keys = [name for name in row if name != "alpha"]
        if key not in probability_keys:
            raise KeyError(f"{ref} 不包含概率单元 {key!r}")

        target = numeric / 100.0
        peers = [name for name in probability_keys if name != key]
        remaining = 1.0 - target
        peer_total = sum(max(0.0, float(row[name])) for name in peers)
        if peers and peer_total > 0:
            allocated = [remaining * max(0.0, float(row[name])) / peer_total for name in peers]
        elif peers:
            allocated = [remaining / len(peers)] * len(peers)
        else:
            allocated = []

        row[key] = target
        for name, value in zip(peers, allocated, strict=True):
            row[name] = value
        if peers:
            row[peers[-1]] += 1.0 - sum(float(row[name]) for name in probability_keys)

        self._adopt(candidate)
        return deepcopy(row)

    def set_confidence(self, ref: RowRef, label: str) -> float:
        confidence_alpha = load_confidence_alpha()
        try:
            alpha = confidence_alpha[label]
        except KeyError as error:
            raise ValueError(f"未知信心强度：{label!r}") from error
        candidate = deepcopy(self._draft)
        self._resolve_row(candidate, ref)["alpha"] = alpha
        self._adopt(candidate)
        return alpha

    def set_expert_value(self, ref: RowRef, key: str, value: float) -> None:
        numeric = self._finite_number(value, "矩阵值")
        candidate = deepcopy(self._draft)
        row = self._resolve_row(candidate, ref)
        if key not in row:
            raise KeyError(f"{ref} 不包含单元 {key!r}")
        row[key] = numeric
        self._adopt(candidate)

    def discard(self) -> None:
        self._draft = deepcopy(self._original)
        self._last_valid = deepcopy(self._original)
        self._validation_error = None

    def accept_saved(self, saved_config: Mapping[str, Any]) -> None:
        copied = deepcopy(dict(saved_config))
        validate(copied)
        self._original = deepcopy(copied)
        self._draft = deepcopy(copied)
        self._last_valid = deepcopy(copied)
        self._validation_error = None

    def replace_draft(self, config: Mapping[str, Any]) -> None:
        candidate = deepcopy(dict(config))
        self._adopt(candidate)

    def _adopt(self, candidate: dict[str, Any]) -> None:
        self._draft = candidate
        try:
            validate(candidate)
        except (ConfigError, KeyError, TypeError, ValueError) as error:
            self._validation_error = str(error)
        else:
            self._last_valid = deepcopy(candidate)
            self._validation_error = None

    @staticmethod
    def _resolve_row(config: dict[str, Any], ref: RowRef) -> dict[str, Any]:
        try:
            section = config[ref.section]
            row = section[ref.row]
            if ref.participant is not None:
                row = row[ref.participant]
        except (KeyError, TypeError) as error:
            raise KeyError(f"未知配置行：{ref}") from error
        if not isinstance(row, dict):
            raise KeyError(f"配置地址不是可编辑行：{ref}")
        return row

    @staticmethod
    def _finite_number(value: float, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label}必须是有限数字")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"{label}必须是有限数字")
        return numeric
