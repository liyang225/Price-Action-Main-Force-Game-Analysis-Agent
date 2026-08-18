"""Structured preanalysis of the purpose behind cached news."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field

from src.integration.model_adapter import (
    ModelRequest,
    StrictModelOutput,
    StructuredModelClient,
)
from src.reasoning.prompt_router import PromptRouter


class SubjectPurposeModelOutput(StrictModelOutput):
    """Validated extraction contract for the subject-purpose prompt."""

    message_type: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    true_purpose: str = Field(min_length=1)
    confidence: Literal["高", "中", "低"]
    signal_type: Literal["分离均衡", "混同均衡"]
    key_evidence: tuple[str, ...] = Field(min_length=1)
    follow_up_expectation: str = Field(min_length=1)


class SubjectPurposeAnalyzer:
    """Inject cached news into the registered subject-purpose prompt."""

    def __init__(self, client: StructuredModelClient, router: PromptRouter) -> None:
        self._client = client
        self._router = router

    def analyze(
        self, sector_name: str, news_items: Sequence[Any]
    ) -> dict[str, Any]:
        if not isinstance(sector_name, str) or not sector_name.strip():
            raise ValueError("sector_name must be a non-empty string")
        serialized_news = [_serialize_news_item(item) for item in news_items]
        if not serialized_news:
            raise ValueError("主体目的分析至少需要一条消息")

        prompt_path = self._router.common("主体目的分析")
        system_prompt = self._router.with_user_experience(
            prompt_path.read_text(encoding="utf-8")
        )
        response = self._client.complete(
            ModelRequest(
                system_prompt=system_prompt,
                user_prompt=json.dumps(
                    {
                        "sector_name": sector_name.strip(),
                        "news_items": serialized_news,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                response_schema=SubjectPurposeModelOutput,
                timeout_seconds=30.0,
                prompt_sources=(str(prompt_path),),
            )
        )
        return {
            "status": "ready",
            "sector_name": sector_name.strip(),
            "news_count": len(serialized_news),
            **response.output.model_dump(mode="json"),
        }


def extract_cached_news_items(cached: Any) -> tuple[Any, ...]:
    """Return the message records from either raw or projected cache forms."""
    if cached is None:
        return ()
    if isinstance(cached, Mapping):
        items = cached.get("items")
        if not isinstance(items, list | tuple):
            return ()
        return tuple(items)
    if isinstance(cached, list | tuple):
        return tuple(cached)
    return (cached,)


def _serialize_news_item(item: Any) -> Any:
    if is_dataclass(item) and not isinstance(item, type):
        return asdict(item)
    if isinstance(item, Mapping):
        return {str(key): _serialize_news_item(value) for key, value in item.items()}
    if isinstance(item, list | tuple):
        return [_serialize_news_item(value) for value in item]
    if item is None or isinstance(item, str | int | float | bool):
        return item
    return str(item)


__all__ = [
    "SubjectPurposeAnalyzer",
    "SubjectPurposeModelOutput",
    "extract_cached_news_items",
]
