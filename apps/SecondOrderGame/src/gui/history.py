"""Validated, atomic configuration saves and a bounded snapshot history."""

from __future__ import annotations

from dataclasses import dataclass
import difflib
import os
from pathlib import Path
import tempfile
from typing import Any

import yaml

from src.config_validator import ConfigError, validate
from src.gui.session import ConfigSession


@dataclass(frozen=True, slots=True)
class Snapshot:
    version: int
    path: Path
    action: str


@dataclass(frozen=True, slots=True)
class VersionEvent:
    version: int
    snapshot: Snapshot
    action: str


class ConfigHistory:
    """Own saves for one ``hmm_prior.yaml`` and its latest snapshots."""

    def __init__(
        self,
        config_path: Path | str,
        history_dir: Path | str | None = None,
        *,
        keep: int = 10,
    ):
        if keep < 1:
            raise ValueError("keep must be at least 1")
        self.config_path = Path(config_path).resolve()
        self.history_dir = (
            Path(history_dir).resolve()
            if history_dir is not None
            else self.config_path.parent / "history" / "hmm_prior"
        )
        self.keep = keep

    def save(self, session: ConfigSession) -> VersionEvent:
        if not session.is_valid:
            raise ConfigError(session.validation_error or "当前编辑无效，不能保存")
        current = self._load(self.config_path)
        if int(current["version"]) != session.base_version:
            raise ConfigError(
                "磁盘配置已被其他进程更新；请丢弃当前会话并重新加载后再保存"
            )
        candidate = session.config
        candidate["version"] = int(current["version"]) + 1
        validate(candidate)

        self._ensure_snapshot(current, "baseline")
        snapshot = self._write_snapshot(candidate, "save")
        try:
            self._atomic_write(self.config_path, candidate)
        except OSError:
            snapshot.path.unlink(missing_ok=True)
            raise
        self._prune()
        session.accept_saved(candidate)
        return VersionEvent(candidate["version"], snapshot, "save")

    def restore(
        self, snapshot: Snapshot | Path | str, session: ConfigSession | None = None
    ) -> VersionEvent:
        selected_path = snapshot.path if isinstance(snapshot, Snapshot) else Path(snapshot)
        selected = self._load(selected_path)
        current = self._load(self.config_path)
        source_version = int(selected["version"])
        restored = selected
        restored["version"] = int(current["version"]) + 1
        validate(restored)

        self._ensure_snapshot(current, "baseline")
        action = f"restore-v{source_version}"
        new_snapshot = self._write_snapshot(restored, action)
        try:
            self._atomic_write(self.config_path, restored)
        except OSError:
            new_snapshot.path.unlink(missing_ok=True)
            raise
        self._prune()
        if session is not None:
            session.accept_saved(restored)
        return VersionEvent(restored["version"], new_snapshot, action)

    def list_snapshots(self) -> list[Snapshot]:
        if not self.history_dir.exists():
            return []
        snapshots: list[Snapshot] = []
        for path in self.history_dir.glob("v*.yaml"):
            try:
                config = self._load(path)
                version = int(config["version"])
            except (ConfigError, KeyError, TypeError, ValueError):
                continue
            name_parts = path.stem.split("-", maxsplit=1)
            action = name_parts[1] if len(name_parts) == 2 else "snapshot"
            snapshots.append(Snapshot(version, path.resolve(), action))
        return sorted(snapshots, key=lambda item: item.version, reverse=True)

    def compare(self, snapshot: Snapshot | Path | str) -> str:
        selected_path = snapshot.path if isinstance(snapshot, Snapshot) else Path(snapshot)
        selected = self._load(selected_path)
        current = self._load(self.config_path)
        before = self._dump(selected).splitlines(keepends=True)
        after = self._dump(current).splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                before,
                after,
                fromfile=selected_path.name,
                tofile=self.config_path.name,
            )
        ) or "当前配置与该快照完全一致。"

    def _ensure_snapshot(self, config: dict[str, Any], action: str) -> Snapshot:
        version = int(config["version"])
        existing = next(
            (item for item in self.list_snapshots() if item.version == version), None
        )
        return existing or self._write_snapshot(config, action)

    def _write_snapshot(self, config: dict[str, Any], action: str) -> Snapshot:
        self.history_dir.mkdir(parents=True, exist_ok=True)
        version = int(config["version"])
        path = self.history_dir / f"v{version:06d}-{action}.yaml"
        self._atomic_write(path, config)
        return Snapshot(version, path.resolve(), action)

    def _prune(self) -> None:
        for snapshot in self.list_snapshots()[self.keep :]:
            snapshot.path.unlink(missing_ok=True)

    @staticmethod
    def _load(path: Path | str) -> dict[str, Any]:
        source = Path(path)
        try:
            loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise ConfigError(f"[{source}] 无法加载配置：{error}") from error
        if not isinstance(loaded, dict):
            raise ConfigError(f"[{source}] 顶层结构应为映射")
        validate(loaded)
        return loaded

    @staticmethod
    def _dump(config: dict[str, Any]) -> str:
        return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)

    def _atomic_write(self, target: Path, config: dict[str, Any]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as output:
                output.write(self._dump(config))
                output.flush()
                os.fsync(output.fileno())
                temporary = Path(output.name)
            os.replace(temporary, target)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)
