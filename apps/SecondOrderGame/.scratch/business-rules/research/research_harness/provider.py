from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from .models import HistoryRequest


@runtime_checkable
class HistoryProvider(Protocol):
    def fetch_history(self, request: HistoryRequest) -> Iterable[Mapping[str, Any]]:
        """Return rows for one instrument and period within the requested range."""
        ...


class InMemoryHistoryProvider:
    """Deterministic provider for fixtures and saved research samples."""

    def __init__(self, rows_by_key: Mapping[Any, Iterable[Mapping[str, Any]]]):
        self._rows_by_key = {
            key: tuple(dict(row) for row in rows) for key, rows in rows_by_key.items()
        }

    def fetch_history(self, request: HistoryRequest) -> Iterable[Mapping[str, Any]]:
        rows = self._rows_by_key.get(
            (request.code, request.period), self._rows_by_key.get(request.code, ())
        )
        return tuple(
            row
            for row in rows
            if request.start <= _row_date(row) <= request.end
        )


def _row_date(row: Mapping[str, Any]) -> date:
    value = row.get("trading_date", row.get("time_key", row.get("timestamp")))
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    if hasattr(value, "date"):
        return value.date()
    raise ValueError("in-memory row needs trading_date, time_key, or timestamp")
