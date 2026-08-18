"""Background preparation before starting the AI analysis worker."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisPrepResult:
    frame: Any
    previous_record: Any | None
    incremental_new_bar_count: int | None
    incremental_detail: str | None


class AnalysisPrepWorker(QThread):
    """Build KlineFrame + incremental base lookup off the UI thread."""

    ready = pyqtSignal(object)  # AnalysisPrepResult
    failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        bars_raw: list[Any],
        symbol: str,
        timeframe: str,
        bar_count: int,
        now_ms: int,
        force_incremental: bool,
        incremental_threshold: int,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._bars_raw = list(bars_raw)
        self._symbol = symbol
        self._timeframe = timeframe
        self._bar_count = bar_count
        self._now_ms = now_ms
        self._force_incremental = force_incremental
        self._incremental_threshold = incremental_threshold

    def run(self) -> None:
        try:
            from pa_agent.data.snapshot import build_display_frame
            from pa_agent.records.analysis_history import (
                compute_incremental_bar_delta,
                find_latest_successful_record,
                format_bar_ts,
            )

            frame = build_display_frame(
                self._bars_raw,
                self._bar_count,
                self._symbol,
                self._timeframe,
                now_ms=self._now_ms,
            )
            if frame is None:
                self.failed.emit("数据不足，无法构建分析快照")
                return

            previous = None
            incremental_new_bar_count: int | None = None
            incremental_detail: str | None = None

            if self._force_incremental or self._incremental_threshold > 0:
                previous = find_latest_successful_record(
                    symbol=self._symbol,
                    timeframe=self._timeframe,
                )
                if previous is not None:
                    delta = compute_incremental_bar_delta(frame, previous)
                    if delta is not None:
                        new_count = delta.new_count
                        if self._force_incremental or new_count <= self._incremental_threshold:
                            incremental_new_bar_count = new_count
                            anchor_label = format_bar_ts(delta.anchor_ts_open)
                            if new_count == 0:
                                incremental_detail = (
                                    f"锚定K线 {anchor_label}，无新增已收盘K线"
                                )
                            elif new_count == 1:
                                incremental_detail = (
                                    f"锚定K线 {anchor_label}，新增1根 "
                                    f"{format_bar_ts(delta.new_bar_ts_opens[0])}"
                                )
                            else:
                                newest = format_bar_ts(delta.new_bar_ts_opens[0])
                                oldest_new = format_bar_ts(delta.new_bar_ts_opens[-1])
                                incremental_detail = (
                                    f"锚定K线 {anchor_label}，新增{new_count}根"
                                    f"（{oldest_new} → {newest}）"
                                )
                        elif not self._force_incremental:
                            previous = None
                    elif self._force_incremental:
                        # Forced incremental mode must never silently fall back
                        # to a full prompt when the historical anchor is absent.
                        previous = None

            self.ready.emit(
                AnalysisPrepResult(
                    frame=frame,
                    previous_record=previous,
                    incremental_new_bar_count=incremental_new_bar_count,
                    incremental_detail=incremental_detail,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Analysis prep failed: %s", exc)
            self.failed.emit(str(exc))
