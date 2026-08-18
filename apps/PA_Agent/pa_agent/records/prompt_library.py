"""Persistent, shared prompt snippets for the live-analysis input."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from pa_agent.config.paths import PROMPT_LIBRARY_JSON_PATH

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptSnippet:
    id: str
    name: str
    text: str


@dataclass
class PromptLibraryState:
    items: list[PromptSnippet] = field(default_factory=list)


class PromptLibraryStore:
    """Read and write prompt snippets shared by every analysis window."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or PROMPT_LIBRARY_JSON_PATH
        self.state = self._load()

    @property
    def items(self) -> tuple[PromptSnippet, ...]:
        return tuple(self.state.items)

    def get(self, item_id: str) -> PromptSnippet | None:
        return next((item for item in self.state.items if item.id == item_id), None)

    def add(self, *, name: str, text: str) -> PromptSnippet:
        item = PromptSnippet(id=uuid4().hex, name=name.strip(), text=text)
        if not item.name:
            raise ValueError("提示词名称不能为空")
        if not item.text.strip():
            raise ValueError("提示词内容不能为空")
        self.state.items.append(item)
        self._save()
        return item

    def update(self, item_id: str, *, name: str, text: str) -> PromptSnippet | None:
        name = name.strip()
        if not name:
            raise ValueError("提示词名称不能为空")
        if not text.strip():
            raise ValueError("提示词内容不能为空")
        for index, item in enumerate(self.state.items):
            if item.id == item_id:
                updated = replace(item, name=name, text=text)
                self.state.items[index] = updated
                self._save()
                return updated
        return None

    def remove(self, item_id: str) -> PromptSnippet | None:
        for index, item in enumerate(self.state.items):
            if item.id == item_id:
                self.state.items.pop(index)
                self._save()
                return item
        return None

    def _load(self) -> PromptLibraryState:
        if not self.path.exists():
            return PromptLibraryState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            raw_items = raw.get("items", []) if isinstance(raw, dict) else []
            items = [
                PromptSnippet(
                    id=str(entry.get("id", "")).strip() or uuid4().hex,
                    name=str(entry.get("name", "")).strip(),
                    text=str(entry.get("text", "")),
                )
                for entry in raw_items
                if isinstance(entry, dict)
                and str(entry.get("name", "")).strip()
                and str(entry.get("text", "")).strip()
            ]
            return PromptLibraryState(items=items)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Failed to load prompt library %s: %s", self.path, exc)
            return PromptLibraryState()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"items": [asdict(item) for item in self.state.items]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
