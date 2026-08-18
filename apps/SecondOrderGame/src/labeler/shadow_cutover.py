"""Independent shadow persistence and staged atomic v2 cutover."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import yaml

from src.labeler.sector_labeler_v2 import SectorV2Label


_DEFAULT_CONFIG = Path(__file__).parents[2] / "config" / "sector_labeler_v2.yaml"


@dataclass(frozen=True, slots=True)
class CutoverResult:
    status: str
    reason: str
    report_path: Path | None = None


class ShadowStateStore:
    """Persist typed v2 labels independently from v1 labels and production C."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS shadow_v2_labels (
                sector TEXT NOT NULL, trading_date TEXT NOT NULL,
                config_version INTEGER NOT NULL, rule_hash TEXT NOT NULL,
                status TEXT NOT NULL, structural_error INTEGER NOT NULL,
                label_json TEXT NOT NULL, PRIMARY KEY(sector, trading_date, rule_hash))"""
            )

    def record_label(self, label: SectorV2Label, *, structural_error: bool = False) -> None:
        if not isinstance(label, SectorV2Label):
            raise TypeError("label must be a SectorV2Label")
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO shadow_v2_labels VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    label.sector_code, label.trading_date, label.config_version,
                    label.rule_hash, label.status, int(structural_error),
                    json.dumps(asdict(label), ensure_ascii=False, sort_keys=True),
                ),
            )

    def readiness(
        self, sector: str, *, rule_hash: str, required_days: int, stable_days: int
    ) -> tuple[bool, str]:
        with self._connect() as connection:
            qualified = connection.execute(
                "SELECT COUNT(*) FROM shadow_v2_labels WHERE sector=? AND rule_hash=? AND status='labeled'",
                (sector, rule_hash),
            ).fetchone()[0]
            recent = connection.execute(
                "SELECT structural_error FROM shadow_v2_labels WHERE sector=? AND rule_hash=? ORDER BY trading_date DESC LIMIT ?",
                (sector, rule_hash, stable_days),
            ).fetchall()
        if qualified < required_days:
            return False, f"{sector} has {qualified}/{required_days} qualifying days"
        if len(recent) < stable_days or any(row[0] for row in recent):
            return False, f"{sector} lacks {stable_days} structurally stable trading days"
        return True, "ready"

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


class ShadowCutoverManager:
    """Stage labels, independent C counts and report before one active-pointer swap."""

    def __init__(
        self,
        store: ShadowStateStore,
        production_directory: Path | str,
        report_directory: Path | str,
        config_path: Path | str = _DEFAULT_CONFIG,
    ) -> None:
        self._store = store
        self._production = Path(production_directory)
        self._reports = Path(report_directory)
        config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        self._required_days = int(config["cutover"]["required_history_days_per_sector"])
        self._stable_days = int(config["cutover"]["required_stable_trading_days"])

    def attempt(
        self,
        registered_sectors: Iterable[str],
        *,
        rule_hash: str,
        relabel_history: Callable[[], Mapping[str, Any]],
        rebuild_c_counts: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> CutoverResult:
        sectors = tuple(dict.fromkeys(registered_sectors))
        if not sectors:
            return CutoverResult("not_ready", "no registered sectors")
        for sector in sectors:
            ready, reason = self._store.readiness(
                sector, rule_hash=rule_hash, required_days=self._required_days,
                stable_days=self._stable_days,
            )
            if not ready:
                return CutoverResult("not_ready", reason)

        self._production.mkdir(parents=True, exist_ok=True)
        self._reports.mkdir(parents=True, exist_ok=True)
        release_name = f"v2-{rule_hash[:12]}"
        staging = Path(tempfile.mkdtemp(prefix=f".{release_name}-", dir=self._production))
        release = self._production / release_name
        report = self._reports / f"sector-labeler-{release_name}.json"
        release_published = False
        report_published = False
        try:
            labels = relabel_history()
            if set(labels) != set(sectors):
                raise ValueError("full relabel did not cover every registered sector")
            c_counts = rebuild_c_counts(labels)
            if not c_counts:
                raise ValueError("rebuilt C counts are empty")
            _write_json(staging / "labels.json", labels)
            _write_json(staging / "c_counts.json", c_counts)
            report_payload = {
                "version": "v2", "rule_hash": rule_hash, "sectors": sectors,
                "label_counts": {key: len(value) for key, value in labels.items()},
                "c_counts_file": "c_counts.json", "labels_file": "labels.json",
            }
            _write_json(staging / "cutover_report.json", report_payload)
            if release.exists() or report.exists():
                raise FileExistsError(f"cutover artifacts already exist for {release_name}")
            staging.replace(release)
            release_published = True
            _atomic_json(report, {**report_payload, "release": str(release.resolve())})
            report_published = True
            _atomic_json(
                self._production / "active.json",
                {"version": "v2", "rule_hash": rule_hash, "release": str(release.resolve()), "report_path": str(report.resolve())},
            )
            return CutoverResult("cutover", "v2 is active", report)
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging)
            if report_published:
                report.unlink(missing_ok=True)
            if release_published and release.exists():
                shutil.rmtree(release)
            return CutoverResult("failed", str(exc))


def _write_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


__all__ = ["CutoverResult", "ShadowCutoverManager", "ShadowStateStore"]
