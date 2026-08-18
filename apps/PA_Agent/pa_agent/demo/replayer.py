"""Timed replay of a saved AnalysisRecord through the same UI signals as live analysis."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from pa_agent.records.schema import AnalysisRecord

# One Unicode codepoint per “token”, like real API streaming.
_CHAR_MS = 16
_STAGE_GAP_MS = 450


from pa_agent.ai.response_extract import content_from_response, reasoning_from_response


def _prompt_parts(messages: list[dict] | None, *, last_user: bool = False) -> tuple[str, str]:
    msgs = messages or []
    system = next((m.get("content", "") for m in msgs if m.get("role") == "system"), "")
    user_messages = reversed(msgs) if last_user else msgs
    user = next((m.get("content", "") for m in user_messages if m.get("role") == "user"), "")
    return str(system), str(user)


def _chars_for_stream(text: str) -> list[str]:
    """Single-character chunks (CJK and ASCII each one cell)."""
    if not text:
        return []
    return list(text)


class DemoReplayer(QObject):
    """Emit the same signals as ``_AnalysisWorker`` on a timer-driven schedule."""

    finished = pyqtSignal(dict)
    record_ready = pyqtSignal(object)
    status_update = pyqtSignal(str)
    reasoning_token = pyqtSignal(str, str)
    content_token = pyqtSignal(str, str)
    stage_prompt_ready = pyqtSignal(str, str, str)
    stage2_files_ready = pyqtSignal(list)
    replay_finished = pyqtSignal()

    def __init__(self, record: AnalysisRecord, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._record = record
        self._steps: list[tuple[int, Callable[[], None]]] = []
        self._index = 0
        self._running = False

    def stop(self) -> None:
        self._running = False
        self._steps.clear()
        self._index = 0

    def start(self) -> None:
        self.stop()
        self._steps = self._build_steps()
        self._index = 0
        self._running = True
        self._run_next()

    def _build_steps(self) -> list[tuple[int, Callable[[], None]]]:
        r = self._record
        steps: list[tuple[int, Callable[[], None]]] = []

        s1_sys, s1_user = _prompt_parts(r.stage1_messages)
        s2_sys, s2_user = _prompt_parts(r.stage2_messages, last_user=True)
        s1_reason = reasoning_from_response(r.stage1_response)
        s2_reason = reasoning_from_response(r.stage2_response)
        s2_content = content_from_response(r.stage2_response)
        strategy = list(r.strategy_files_used or [])

        def add(delay: int, fn: Callable[[], None]) -> None:
            steps.append((delay, fn))

        add(_STAGE_GAP_MS, lambda: self.status_update.emit("阶段一分析中…"))
        add(80, lambda: self.stage_prompt_ready.emit("stage1", s1_sys, s1_user))
        for ch in _chars_for_stream(s1_reason):
            add(_CHAR_MS, lambda c=ch: self.reasoning_token.emit("stage1", c))
        add(_STAGE_GAP_MS, lambda: self.status_update.emit("阶段一完成"))

        if strategy or r.stage2_decision:
            add(200, lambda: self.stage2_files_ready.emit(strategy))
            add(_STAGE_GAP_MS, lambda: self.status_update.emit("阶段二分析中…"))
            add(80, lambda: self.stage_prompt_ready.emit("stage2", s2_sys, s2_user))
            for ch in _chars_for_stream(s2_reason):
                add(_CHAR_MS, lambda c=ch: self.reasoning_token.emit("stage2", c))
            if not s2_reason and s2_content:
                for ch in _chars_for_stream(s2_content):
                    add(_CHAR_MS, lambda c=ch, s="stage2": self.content_token.emit(s, c))
            add(_STAGE_GAP_MS, lambda: self.status_update.emit("阶段二完成"))

        # Match real worker: stream ends, then record persisted, then record_ready → finished.
        add(300, lambda: self.status_update.emit("记录已保存"))
        add(120, lambda: self.record_ready.emit(r))
        add(200, self._emit_finished)
        return steps

    def _emit_finished(self) -> None:
        decision = self._record.stage2_decision or {}
        self.finished.emit(decision if isinstance(decision, dict) else {})

    def _run_next(self) -> None:
        if not self._running:
            return
        if self._index >= len(self._steps):
            self._running = False
            self.replay_finished.emit()
            return
        delay, action = self._steps[self._index]
        self._index += 1
        action()
        QTimer.singleShot(delay, self._run_next)
