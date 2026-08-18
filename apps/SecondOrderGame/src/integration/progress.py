"""Framework-neutral progress event stream for the reasoning pipeline.

The second-order backend emits ``ProgressEvent`` objects while it reasons and
computes; a host UI (or test) consumes them through a ``ProgressSink``.  The
sink keeps a bounded, in-memory recording so a completed run can persist its
full progress transcript for later replay without coupling the domain modules
to any UI framework.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Any, Callable, Mapping

VALID_KINDS = ("stage", "thinking", "content", "info", "error")

_MAX_RECORDED_EVENTS = 10_000


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One immutable, human-readable progress item.

    ``kind`` semantics:
      - ``stage``   a pipeline milestone carrying one natural-language conclusion
      - ``thinking`` a model reasoning chunk (streamed)
      - ``content``  a model content/answer chunk (streamed)
      - ``info``     a neutral process note
      - ``error``    an error line (rendered distinctly by consumers)
    """

    ts: datetime
    symbol: str = ""
    kind: str = "info"
    stage: str = ""
    message: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.ts, datetime):
            raise TypeError("ts must be a datetime")
        if self.kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS!r}, got {self.kind!r}")
        for name in ("symbol", "kind", "stage", "message", "source"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "symbol": self.symbol,
            "kind": self.kind,
            "stage": self.stage,
            "message": self.message,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProgressEvent":
        if not isinstance(data, Mapping):
            raise TypeError("progress event data must be a mapping")
        ts = data.get("ts")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if ts is None:
            ts = datetime.now()
        return cls(
            ts=ts,
            symbol=str(data.get("symbol") or ""),
            kind=str(data.get("kind") or "info"),
            stage=str(data.get("stage") or ""),
            message=str(data.get("message") or ""),
            source=str(data.get("source") or ""),
        )


class ProgressSink:
    """Subscribe-and-record hub; thread-safe, bounded, side-effect free."""

    def __init__(self, *, max_events: int = _MAX_RECORDED_EVENTS) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
            raise ValueError("max_events must be a positive integer")
        self._max_events = max_events
        self._subscribers: list[Callable[[ProgressEvent], None]] = []
        self._events: list[ProgressEvent] = []
        self._lock = RLock()

    def subscribe(self, callback: Callable[[ProgressEvent], None]) -> None:
        if not callable(callback):
            raise TypeError("subscriber must be callable")
        with self._lock:
            self._subscribers.append(callback)

    def emit(self, event: ProgressEvent) -> None:
        if not isinstance(event, ProgressEvent):
            raise TypeError("event must be a ProgressEvent")
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_events:
                del self._events[: len(self._events) - self._max_events]
            subscribers = tuple(self._subscribers)
        for callback in subscribers:
            callback(event)

    def events(self) -> list[ProgressEvent]:
        with self._lock:
            return list(self._events)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


__all__ = ["ProgressEvent", "ProgressSink", "VALID_KINDS"]
