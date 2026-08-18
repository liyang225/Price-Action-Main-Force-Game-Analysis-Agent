"""Threading utilities: CancelToken and OrchestratorEvent."""
from __future__ import annotations

import threading
from enum import Enum, auto


class CancelToken:
    """A simple cancellation token backed by threading.Event."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        """Signal cancellation."""
        self._event.set()

    def is_set(self) -> bool:
        """Return True if cancellation has been requested."""
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until set or timeout. Returns True if set."""
        return self._event.wait(timeout=timeout)

    def clear(self) -> None:
        """Reset the token (for reuse)."""
        self._event.clear()


class OrchestratorEvent(Enum):
    """Events emitted by TwoStageOrchestrator during a submission."""
    Stage1Started = auto()
    Stage1Retry = auto()
    Stage1Done = auto()
    Stage1Failed = auto()
    Stage2Started = auto()
    Stage2Retry = auto()
    Stage2Done = auto()
    Stage2Failed = auto()
    RecordSaved = auto()
    Cancelled = auto()
    InsufficientData = auto()
