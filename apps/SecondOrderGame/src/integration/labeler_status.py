"""Structured runtime status of the post-hoc OHLCV labelers.

PA's overview tab renders two fields from this module:

* **加载状态** (``LoadState``) — whether the labeler's rule configuration and
  labeling scope are ready.  Advanced by the service constructor thread.
* **运行状态** (``RunState``) — what the background catch-up thread is doing.
  Advanced by the catch-up daemon thread.

The tracker is thread-safe and UI-consumable: any consumer may poll
:meth:`LabelerStatusTracker.snapshot` or subscribe to change callbacks.  The
status object is immutable, so a snapshot can be passed across threads
without copying.

OWN-WORLD: second-order UI renders statuses as colored pills — green for
ready/done, blue for in-progress, red for failure/unavailable, gray for
idle/skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from threading import Lock
from typing import Any, Callable, Mapping


class LoadState(str, Enum):
    """Whether the labeler's rules and labeling scope are ready."""

    NOT_LOADED = "not_loaded"  # 未加载：服务刚构造，尚未开始准备
    LOADING = "loading"  # 加载中：正在读取规则配置与标注范围
    LOADED = "loaded"  # 已加载：规则与范围就绪
    LOAD_FAILED = "load_failed"  # 加载失败：范围或数据源不可用


class RunState(str, Enum):
    """What the labeler catch-up thread is doing (or last did)."""

    IDLE = "idle"  # 空闲：尚未运行补跑
    RUNNING = "running"  # 运行中：补跑线程正在工作
    COMPLETED = "completed"  # 已完成：补跑完成且无可报告的失败
    PARTIAL_FAILURE = "partial_failure"  # 部分失败：存在失败交易日
    SKIPPED = "skipped"  # 已跳过：缺少标注范围等前置条件
    SOURCE_UNAVAILABLE = "source_unavailable"  # 数据源不可用


_LOAD_LABELS: Mapping[LoadState, str] = {
    LoadState.NOT_LOADED: "未加载",
    LoadState.LOADING: "加载中",
    LoadState.LOADED: "已加载",
    LoadState.LOAD_FAILED: "加载失败",
}

_RUN_LABELS: Mapping[RunState, str] = {
    RunState.IDLE: "空闲",
    RunState.RUNNING: "运行中",
    RunState.COMPLETED: "已完成",
    RunState.PARTIAL_FAILURE: "部分失败",
    RunState.SKIPPED: "已跳过",
    RunState.SOURCE_UNAVAILABLE: "数据源不可用",
}


@dataclass(frozen=True, slots=True)
class LabelerStatus:
    """Immutable snapshot of both labeler status fields."""

    load_state: LoadState = LoadState.NOT_LOADED
    run_state: RunState = RunState.IDLE
    load_message: str = ""
    run_message: str = ""
    last_updated: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def load_label(self) -> str:
        return _LOAD_LABELS[self.load_state]

    @property
    def run_label(self) -> str:
        return _RUN_LABELS[self.run_state]

    def to_dict(self) -> dict[str, Any]:
        return {
            "load_state": self.load_state.value,
            "load_label": self.load_label,
            "run_state": self.run_state.value,
            "run_label": self.run_label,
            "load_message": self.load_message,
            "run_message": self.run_message,
            "last_updated": self.last_updated,
            "details": dict(self.details),
        }


StatusCallback = Callable[[LabelerStatus], None]


class LabelerStatusTracker:
    """Thread-safe status holder advanced by the labeler catch-up flow."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._status = LabelerStatus()
        self._subscribers: list[StatusCallback] = []
        self._running = False

    # -- reads ---------------------------------------------------------------

    def snapshot(self) -> LabelerStatus:
        with self._lock:
            return self._status

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def subscribe(self, callback: StatusCallback) -> None:
        """Register a callback invoked (after lock release) on each change."""
        with self._lock:
            self._subscribers.append(callback)

    # -- writes --------------------------------------------------------------

    def set_load(self, state: LoadState, message: str = "") -> None:
        self._update(load_state=state, load_message=message)

    def set_run(
        self,
        state: RunState,
        message: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self._update(
            run_state=state,
            run_message=message,
            details={**self._status.details, **(details or {})},
        )

    def mark_running(self) -> bool:
        """Claim the single running slot; False when another thread is active."""
        with self._lock:
            if self._running:
                return False
            self._running = True
            return True

    def clear_running(self) -> None:
        with self._lock:
            self._running = False

    # -- internals -----------------------------------------------------------

    def _update(self, **changes: Any) -> None:
        with self._lock:
            current = self._status
            merged = dict(current.to_dict())
            merged.update(changes)
            merged["last_updated"] = datetime.now().isoformat(timespec="seconds")
            self._status = LabelerStatus(
                load_state=LoadState(merged["load_state"]),
                run_state=RunState(merged["run_state"]),
                load_message=str(merged["load_message"]),
                run_message=str(merged["run_message"]),
                last_updated=merged["last_updated"],
                details=dict(merged["details"]),
            )
            subscribers = tuple(self._subscribers)
        for callback in subscribers:
            try:
                callback(self._status)
            except Exception:  # noqa: BLE001 — a bad subscriber must not break status
                pass


__all__ = [
    "LabelerStatus",
    "LabelerStatusTracker",
    "LoadState",
    "RunState",
    "StatusCallback",
]
