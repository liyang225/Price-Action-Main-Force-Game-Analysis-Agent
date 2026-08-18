"""Persistent settlement rules used when evaluating archived trade plans."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pa_agent.config.paths import (
    INSTRUMENT_TRADE_RULES_JSON_PATH,
    TRADE_ENTRY_OVERRIDES_JSON_PATH,
)


SETTLEMENT_UNSET = "unset"
SETTLEMENT_T0 = "t0"
SETTLEMENT_T1 = "t1"
SETTLEMENT_MODES = frozenset((SETTLEMENT_UNSET, SETTLEMENT_T0, SETTLEMENT_T1))


def normalize_entry_tolerance_ticks(value: object) -> int:
    try:
        ticks = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(ticks, 100))


@dataclass(frozen=True)
class ManualEntryOverride:
    timestamp_ms: int
    price: float


def normalize_instrument_key(symbol: str) -> str:
    """Return the stable per-instrument key used by the history panel."""
    return "".join(str(symbol or "").strip().upper().split())


def normalize_settlement_mode(value: object) -> str:
    """Coerce stored/UI values to an explicit settlement mode."""
    text = str(value or "").strip().lower().replace("+", "")
    aliases = {
        "t0": SETTLEMENT_T0,
        "0": SETTLEMENT_T0,
        "t1": SETTLEMENT_T1,
        "1": SETTLEMENT_T1,
        "": SETTLEMENT_UNSET,
        "unset": SETTLEMENT_UNSET,
        "未设置": SETTLEMENT_UNSET,
    }
    return aliases.get(text, SETTLEMENT_UNSET)


class InstrumentTradeRuleStore:
    """Store explicit T+0/T+1 choices separately from analysis records."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or INSTRUMENT_TRADE_RULES_JSON_PATH
        self._rules, self._entry_tolerances = self._load()

    def mode_for(self, symbol: str) -> str:
        return self._rules.get(normalize_instrument_key(symbol), SETTLEMENT_UNSET)

    def set_mode(self, symbol: str, mode: object) -> None:
        key = normalize_instrument_key(symbol)
        if not key:
            return
        normalized = normalize_settlement_mode(mode)
        if normalized == SETTLEMENT_UNSET:
            changed = self._rules.pop(key, None) is not None
        else:
            changed = self._rules.get(key) != normalized
            self._rules[key] = normalized
        if changed:
            self._save()

    def entry_tolerance_ticks_for(self, symbol: str) -> int:
        return self._entry_tolerances.get(normalize_instrument_key(symbol), 0)

    def set_entry_tolerance_ticks(self, symbol: str, ticks: object) -> None:
        key = normalize_instrument_key(symbol)
        if not key:
            return
        normalized = normalize_entry_tolerance_ticks(ticks)
        if normalized == 0:
            changed = self._entry_tolerances.pop(key, None) is not None
        else:
            changed = self._entry_tolerances.get(key) != normalized
            self._entry_tolerances[key] = normalized
        if changed:
            self._save()

    def _load(self) -> tuple[dict[str, str], dict[str, int]]:
        if not self.path.exists():
            return {}, {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}, {}
        rules = raw.get("rules", {}) if isinstance(raw, dict) else {}
        tolerances = raw.get("entry_tolerance_ticks", {}) if isinstance(raw, dict) else {}
        if not isinstance(rules, dict):
            rules = {}
        if not isinstance(tolerances, dict):
            tolerances = {}
        normalized: dict[str, str] = {}
        for symbol, mode in rules.items():
            key = normalize_instrument_key(str(symbol))
            value = normalize_settlement_mode(mode)
            if key and value != SETTLEMENT_UNSET:
                normalized[key] = value
        normalized_tolerances: dict[str, int] = {}
        for symbol, ticks in tolerances.items():
            key = normalize_instrument_key(str(symbol))
            value = normalize_entry_tolerance_ticks(ticks)
            if key and value:
                normalized_tolerances[key] = value
        return normalized, normalized_tolerances

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "rules": self._rules,
                    "entry_tolerance_ticks": self._entry_tolerances,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


class TradeEntryOverrideStore:
    """Persist manually confirmed entry times and prices by history record."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or TRADE_ENTRY_OVERRIDES_JSON_PATH
        self._entries = self._load()

    @staticmethod
    def _key(record_path: Path) -> str:
        return str(Path(record_path).resolve())

    def override_for(self, record_path: Path) -> ManualEntryOverride | None:
        raw = self._entries.get(self._key(record_path))
        if not isinstance(raw, dict):
            return None
        try:
            timestamp_ms = int(raw["timestamp_ms"])
            price = float(raw["price"])
        except (KeyError, TypeError, ValueError):
            return None
        if timestamp_ms <= 0 or price <= 0:
            return None
        return ManualEntryOverride(timestamp_ms=timestamp_ms, price=price)

    def all_overrides(self) -> dict[str, ManualEntryOverride]:
        overrides: dict[str, ManualEntryOverride] = {}
        for key in self._entries:
            override = self.override_for(Path(key))
            if override is not None:
                overrides[key] = override
        return overrides

    def set_override(self, record_path: Path, *, timestamp_ms: int, price: float) -> None:
        if int(timestamp_ms) <= 0 or float(price) <= 0:
            return
        self._entries[self._key(record_path)] = {
            "timestamp_ms": int(timestamp_ms),
            "price": float(price),
        }
        self._save()

    def clear_override(self, record_path: Path) -> None:
        if self._entries.pop(self._key(record_path), None) is not None:
            self._save()

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return {}
        entries = raw.get("entries", {}) if isinstance(raw, dict) else {}
        return entries if isinstance(entries, dict) else {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"entries": self._entries}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
