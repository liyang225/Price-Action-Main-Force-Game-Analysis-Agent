"""Persistent watchlist and analysis-pool data used by the workspace UI."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from pa_agent.config.paths import WATCHLIST_JSON_PATH

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatchlistItem:
    """One manually maintained analysis target."""

    id: str
    name: str
    symbol: str
    data_source: str = "tradingview"
    exchange: str = ""
    timeframe: str = "15m"

    @property
    def display_name(self) -> str:
        return self.name.strip() or self.symbol.strip()


@dataclass
class WatchlistState:
    """Serialized state. Runtime analysis status deliberately stays out of it."""

    items: list[WatchlistItem] = field(default_factory=list)
    analysis_pool_ids: list[str] = field(default_factory=list)


class WatchlistStore:
    """Read and write watchlist data without coupling it to Qt widgets."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or WATCHLIST_JSON_PATH
        self.state = self._load()

    @property
    def items(self) -> tuple[WatchlistItem, ...]:
        return tuple(self.state.items)

    @property
    def analysis_pool_ids(self) -> frozenset[str]:
        return frozenset(self.state.analysis_pool_ids)

    def get(self, item_id: str) -> WatchlistItem | None:
        return next((item for item in self.state.items if item.id == item_id), None)

    def in_analysis_pool(self, item_id: str) -> bool:
        return item_id in self.analysis_pool_ids

    def add(
        self,
        *,
        name: str,
        symbol: str,
        data_source: str = "tradingview",
        exchange: str = "",
        timeframe: str = "15m",
    ) -> WatchlistItem:
        item = WatchlistItem(
            id=uuid4().hex,
            name=name.strip(),
            symbol=symbol.strip(),
            data_source=data_source.strip() or "tradingview",
            exchange=exchange.strip(),
            timeframe=timeframe.strip() or "15m",
        )
        if not item.symbol:
            raise ValueError("股票代码不能为空")
        self.state.items.append(item)
        self._save()
        return item

    def add_to_analysis_pool(self, item_ids: list[str]) -> list[str]:
        known_ids = {item.id for item in self.state.items}
        added: list[str] = []
        for item_id in item_ids:
            if item_id in known_ids and item_id not in self.state.analysis_pool_ids:
                self.state.analysis_pool_ids.append(item_id)
                added.append(item_id)
        if added:
            self._save()
        return added

    def update(
        self,
        item_id: str,
        *,
        name: str | None = None,
        data_source: str | None = None,
        exchange: str | None = None,
        timeframe: str | None = None,
    ) -> WatchlistItem | None:
        """Persist editable watchlist fields while preserving the stock code."""
        for index, item in enumerate(self.state.items):
            if item.id != item_id:
                continue
            updated = replace(
                item,
                name=name.strip() if name is not None else item.name,
                data_source=(data_source.strip() or "tradingview")
                if data_source is not None
                else item.data_source,
                exchange=exchange.strip() if exchange is not None else item.exchange,
                timeframe=(timeframe.strip() or "15m")
                if timeframe is not None
                else item.timeframe,
            )
            self.state.items[index] = updated
            self._save()
            return updated
        return None

    def remove(self, item_id: str) -> WatchlistItem | None:
        """Delete a stock and remove its analysis-pool membership."""
        for index, item in enumerate(self.state.items):
            if item.id == item_id:
                self.state.items.pop(index)
                self.state.analysis_pool_ids = [
                    pool_id for pool_id in self.state.analysis_pool_ids if pool_id != item_id
                ]
                self._save()
                return item
        return None

    def remove_from_analysis_pool(self, item_ids: list[str]) -> list[str]:
        remove_ids = set(item_ids)
        removed = [item_id for item_id in self.state.analysis_pool_ids if item_id in remove_ids]
        if removed:
            self.state.analysis_pool_ids = [
                item_id for item_id in self.state.analysis_pool_ids if item_id not in remove_ids
            ]
            self._save()
        return removed

    def analysis_pool_items(self) -> tuple[WatchlistItem, ...]:
        pool_ids = self.analysis_pool_ids
        return tuple(item for item in self.state.items if item.id in pool_ids)

    def _load(self) -> WatchlistState:
        if not self.path.exists():
            return WatchlistState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            raw_items = raw.get("items", []) if isinstance(raw, dict) else []
            items: list[WatchlistItem] = []
            seen_ids: set[str] = set()
            for entry in raw_items:
                if not isinstance(entry, dict):
                    continue
                symbol = str(entry.get("symbol", "")).strip()
                if not symbol:
                    continue
                item_id = str(entry.get("id", "")).strip() or uuid4().hex
                if item_id in seen_ids:
                    item_id = uuid4().hex
                seen_ids.add(item_id)
                items.append(
                    WatchlistItem(
                        id=item_id,
                        name=str(entry.get("name", "")).strip(),
                        symbol=symbol,
                        data_source=str(entry.get("data_source", "tradingview")).strip()
                        or "tradingview",
                        exchange=str(entry.get("exchange", "")).strip(),
                        timeframe=str(entry.get("timeframe", "15m")).strip() or "15m",
                    )
                )
            raw_pool_ids = raw.get("analysis_pool_ids", []) if isinstance(raw, dict) else []
            pool_ids = [
                str(item_id)
                for item_id in raw_pool_ids
                if str(item_id) in seen_ids
            ]
            return WatchlistState(items=items, analysis_pool_ids=list(dict.fromkeys(pool_ids)))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Failed to load watchlist %s: %s", self.path, exc)
            return WatchlistState()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "items": [asdict(item) for item in self.state.items],
            "analysis_pool_ids": self.state.analysis_pool_ids,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
