"""Tavily Search adapter for provider-neutral news records."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os
from typing import Any
from urllib.request import Request, urlopen

from .models import NewsItem
from .protocol import DataSourceError


TAVILY_SEARCH_ENDPOINT = "https://api.tavily.com/search"


class TavilyNewsProvider:
    """Adapt Tavily Search into provider-neutral ``NewsItem`` values."""

    def __init__(
        self,
        api_key: str,
        *,
        max_results: int = 10,
        timeout_seconds: float = 20.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Tavily API key must be a non-empty string")
        if not isinstance(max_results, int) or isinstance(max_results, bool):
            raise TypeError("max_results must be an integer")
        if not 1 <= max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._api_key = api_key.strip()
        self._max_results = max_results
        self._timeout_seconds = float(timeout_seconds)
        self._opener = opener

    @classmethod
    def from_environment(
        cls, variable: str = "TAVILY_API_KEY", **kwargs: Any
    ) -> "TavilyNewsProvider":
        api_key = os.environ.get(variable)
        if not api_key:
            raise DataSourceError(f"Tavily API key environment variable is missing: {variable}")
        return cls(api_key, **kwargs)

    def search_news(self, keyword: str) -> list[NewsItem]:
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError("keyword must be a non-empty string")
        request = Request(
            TAVILY_SEARCH_ENDPOINT,
            data=json.dumps(
                {
                    "query": keyword.strip(),
                    "search_depth": "basic",
                    "max_results": self._max_results,
                    "topic": "news",
                    "time_range": "day",
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": False,
                }
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                status = getattr(response, "status", 200)
                body = response.read()
        except Exception as exc:
            raise DataSourceError(f"Tavily search request failed: {exc}") from exc
        if status != 200:
            raise DataSourceError(f"Tavily search returned HTTP {status}")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataSourceError("Tavily search returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise DataSourceError("Tavily search response must be a JSON object")
        results = payload.get("results")
        if not isinstance(results, list):
            raise DataSourceError("Tavily search response is missing results")

        items: list[NewsItem] = []
        for result in results:
            if not isinstance(result, Mapping):
                raise DataSourceError("Tavily search result must be a JSON object")
            title = result.get("title")
            url = result.get("url")
            if not isinstance(title, str) or not title.strip():
                raise DataSourceError("Tavily search result is missing title")
            if not isinstance(url, str) or not url.strip():
                raise DataSourceError("Tavily search result is missing URL")
            content = result.get("content", "")
            published_date = result.get("published_date", "")
            items.append(
                NewsItem(
                    title=title.strip(),
                    snippet=content if isinstance(content, str) else str(content),
                    url=url.strip(),
                    published_date=(
                        published_date
                        if isinstance(published_date, str)
                        else str(published_date)
                    ),
                    source="Tavily",
                )
            )
        return items
