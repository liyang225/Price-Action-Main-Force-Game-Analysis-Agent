"""Daily shared cache for analysis materials and their close-time archives."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import date, datetime, time
import json
from pathlib import Path
import tempfile
from threading import RLock
from types import MappingProxyType
from typing import Any


MARKET_CLOSE = time(15, 0)


class DailyMaterialCacheClosedError(RuntimeError):
    """Raised when background work writes after the current day was archived."""


class DailyMaterialArchiveError(RuntimeError):
    """Raised when the close-time archive cannot be written safely."""


@dataclass(frozen=True, slots=True)
class DailyMaterialSnapshot:
    """An immutable decision-time view of one trading day's materials."""

    trading_date: date
    materials: Mapping[str, Mapping[str, Any]]


class DailyMaterialCache:
    """Keep shared daily materials in memory until an explicit close-time archive."""

    def __init__(
        self,
        archive_directory: str | Path,
        *,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._archive_directory = Path(archive_directory)
        self._clock = clock
        self._lock = RLock()
        self._trading_date: date | None = None
        self._materials: dict[str, dict[str, Any]] = {}
        self._decision_snapshot: DailyMaterialSnapshot | None = None
        self._archive_path: Path | None = None

    def put(self, category: str, key: str, material: Any) -> None:
        """Replace one named material during the background fill phase."""

        _validate_name(category, "category")
        _validate_name(key, "key")
        _validate_material(material)
        with self._lock:
            self._ensure_current_day()
            if self._archive_path is not None:
                raise DailyMaterialCacheClosedError(
                    "daily material cache is closed after its archive was written"
                )
            self._materials.setdefault(category, {})[key] = deepcopy(material)

    def get(self, category: str, key: str, default: Any = None) -> Any:
        """Read one live material without freezing or exposing cache state."""

        _validate_name(category, "category")
        _validate_name(key, "key")
        with self._lock:
            self._ensure_current_day()
            value = self._materials.get(category, {}).get(key, default)
            return deepcopy(value)

    def preview(self) -> dict[str, dict[str, Any]]:
        """Return a detached live-cache projection for lifecycle diagnostics."""

        with self._lock:
            self._ensure_current_day()
            return deepcopy(self._materials)

    def snapshot(self, *, refresh: bool = False) -> DailyMaterialSnapshot:
        """Return a stable, read-only view for one decision.

        ``refresh=True`` starts a new decision view from the materials that
        have been filled since the previous view.  It does not mutate an
        already returned snapshot, which lets a long-running process reuse
        one daily cache without leaking a prior decision into the next one.
        """

        with self._lock:
            trading_date = self._ensure_current_day()
            if refresh or self._decision_snapshot is None:
                self._decision_snapshot = self._snapshot_for(trading_date)
            return self._decision_snapshot

    def put_many_and_snapshot(
        self, materials: Mapping[str, Mapping[str, Any]]
    ) -> DailyMaterialSnapshot:
        """Atomically fill one decision bundle and freeze its complete view."""
        if not isinstance(materials, Mapping) or not materials:
            raise ValueError("materials must be a non-empty category mapping")
        prepared: dict[str, dict[str, Any]] = {}
        for category, items in materials.items():
            _validate_name(category, "category")
            if not isinstance(items, Mapping) or not items:
                raise ValueError("each material category must contain named items")
            prepared[category] = {}
            for key, material in items.items():
                _validate_name(key, "key")
                _validate_material(material)
                prepared[category][key] = deepcopy(material)

        with self._lock:
            trading_date = self._ensure_current_day()
            if self._archive_path is not None:
                raise DailyMaterialCacheClosedError(
                    "daily material cache is closed after its archive was written"
                )
            for category, items in prepared.items():
                self._materials.setdefault(category, {}).update(items)
            self._decision_snapshot = self._snapshot_for(trading_date)
            return self._decision_snapshot

    def archive(self) -> Path:
        """Atomically serialize the current day's material after the close."""

        with self._lock:
            now = self._now()
            trading_date = self._ensure_current_day(now)
            if now.timetz().replace(tzinfo=None) < MARKET_CLOSE:
                raise ValueError("daily material cache can only be archived at or after 15:00")
            if self._archive_path is not None:
                return self._archive_path

            archive_path = self._write_archive(self._snapshot_for(trading_date))
            self._archive_path = archive_path
            return archive_path

    def status(self) -> dict[str, Any]:
        """Return a UI-safe lifecycle summary without exposing mutable state."""
        with self._lock:
            trading_date = self._ensure_current_day()
            return {
                "trading_date": trading_date.isoformat(),
                "state": "archived" if self._archive_path is not None else "filling",
                "categories": {
                    category: len(items) for category, items in self._materials.items()
                },
                "decision_snapshot_created": self._decision_snapshot is not None,
                "archive_path": str(self._archive_path) if self._archive_path else None,
            }

    def _ensure_current_day(self, now: datetime | None = None) -> date:
        current_day = (now or self._now()).date()
        if self._trading_date != current_day:
            self._trading_date = current_day
            self._materials = {}
            self._decision_snapshot = None
            self._archive_path = None
        return current_day

    def _snapshot_for(self, trading_date: date) -> DailyMaterialSnapshot:
        return DailyMaterialSnapshot(
            trading_date=trading_date,
            materials=_freeze(deepcopy(self._materials)),
        )

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime):
            raise TypeError("clock must return a datetime")
        return now

    def _write_archive(self, snapshot: DailyMaterialSnapshot) -> Path:
        temporary_path: Path | None = None
        try:
            self._archive_directory.mkdir(parents=True, exist_ok=True)
            archive_path = self._archive_directory / f"{snapshot.trading_date.isoformat()}.json"
            if archive_path.exists():
                raise FileExistsError(f"daily material archive already exists: {archive_path}")
            payload = {
                "trading_date": snapshot.trading_date.isoformat(),
                "materials": snapshot.materials,
            }
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._archive_directory,
                prefix=f".{snapshot.trading_date.isoformat()}-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(payload, temporary_file, ensure_ascii=False, default=_json_default)
            temporary_path.replace(archive_path)
            return archive_path
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise DailyMaterialArchiveError("failed to write daily material archive") from exc


def _validate_name(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _validate_material(value: Any) -> None:
    if value is None or isinstance(value, str | int | float | bool | date | datetime):
        return
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("material mapping keys must be strings")
        for item in value.values():
            _validate_material(item)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _validate_material(item)
        return
    if is_dataclass(value) and not isinstance(value, type):
        if not value.__dataclass_params__.frozen:
            raise TypeError("material dataclasses must be immutable")
        for field in fields(value):
            _validate_dataclass_field(getattr(value, field.name))
        return
    raise TypeError("material must be recursively immutable")


def _validate_dataclass_field(value: Any) -> None:
    if value is None or isinstance(value, str | int | float | bool | date | datetime):
        return
    if isinstance(value, tuple):
        for item in value:
            _validate_dataclass_field(item)
        return
    if is_dataclass(value) and not isinstance(value, type):
        _validate_material(value)
        return
    raise TypeError("material dataclass fields must be recursively immutable")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def _json_default(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, tuple | frozenset):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")
