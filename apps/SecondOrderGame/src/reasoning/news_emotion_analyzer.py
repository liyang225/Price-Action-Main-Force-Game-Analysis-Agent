"""LLM-scored news sentiment, replacing the weak static lexical dictionary.

The model reads each message and emits a signed strength in ``[-8, 8]`` plus a
relevance and source-credibility estimate in ``[0, 1]``.  These three values
are combined with the deterministic validity factor by
``NewsSentimentAnalyzer`` afterwards.  See ADR-0023/0024.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import math
from typing import Any, Mapping, Sequence

from pydantic import Field

from src.data.models import ModelNewsJudgment
from src.integration.model_adapter import (
    ModelRequest,
    StrictModelOutput,
    StructuredModelClient,
)
from src.reasoning.prompt_router import PromptRouter


class NewsEmotionItem(StrictModelOutput):
    index: int = Field(ge=0)
    sentiment_score: float
    relevance: float
    credibility: float


class NewsEmotionModelOutput(StrictModelOutput):
    items: tuple[NewsEmotionItem, ...] = Field(min_length=1)


class NewsEmotionAnalyzer:
    """Ask the model for one sentiment judgment per cached news item."""

    def __init__(self, client: StructuredModelClient, router: PromptRouter) -> None:
        self._client = client
        self._router = router

    def analyze(
        self,
        sector_name: str,
        target_security: str | None,
        news_items: Sequence[Any],
    ) -> tuple[ModelNewsJudgment, ...]:
        """Return one judgment per ``news_items`` entry, in input order."""
        serialized = [_serialize(item) for item in news_items]
        if not serialized:
            return ()
        prompt_path = self._router.common("新闻情绪评分")
        system_prompt = self._router.with_user_experience(
            prompt_path.read_text(encoding="utf-8")
        )
        response = self._client.complete(
            ModelRequest(
                system_prompt=system_prompt,
                user_prompt=json.dumps(
                    {
                        "sector_name": str(sector_name).strip(),
                        "target_security": str(target_security or "").strip(),
                        "news_items": serialized,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                response_schema=NewsEmotionModelOutput,
                timeout_seconds=30.0,
                prompt_sources=(str(prompt_path),),
                allow_numeric_fields=frozenset(
                    {"index", "sentiment_score", "relevance", "credibility"}
                ),
            )
        )
        by_index = {item.index: item for item in response.output.items}
        return tuple(
            ModelNewsJudgment(
                sentiment_score=_clamp_score(
                    by_index[index].sentiment_score if index in by_index else 0.0
                ),
                relevance=_clamp_unit(
                    by_index[index].relevance if index in by_index else None
                ),
                credibility=_clamp_unit(
                    by_index[index].credibility if index in by_index else None
                ),
            )
            for index in range(len(serialized))
        )


def _serialize(item: Any) -> dict[str, Any]:
    if is_dataclass(item) and not isinstance(item, type):
        raw = asdict(item)
    elif isinstance(item, Mapping):
        raw = dict(item)
    else:
        raw = {"title": str(item)}
    return {
        "title": str(raw.get("title") or ""),
        "snippet": str(raw.get("snippet") or ""),
        "source": str(raw.get("source") or ""),
        "published_date": str(raw.get("published_date") or ""),
    }


def _clamp_score(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    number = float(value)
    if not math.isfinite(number):
        return 0.0
    return max(-8.0, min(8.0, number))


def _clamp_unit(value: Any) -> float | None:
    """Clamp a soft factor to [0, 1]; ``None`` stays ``None`` (program fallback)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return max(0.0, min(1.0, number))


__all__ = ["NewsEmotionAnalyzer", "NewsEmotionItem", "NewsEmotionModelOutput"]
