"""Embedded SecondOrderGame workspace hosted by a PA instrument page."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from threading import Event, Thread
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Mapping

from PyQt6.QtCore import QTime, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QTimeEdit,
)
from PyQt6.QtCore import Qt

from pa_agent.data.base import KlineBar, KlineFrame
from pa_agent.data.snapshot import compute_indicators
from pa_agent.gui.second_order_chart import (
    SecondOrderGameChart,
    create_second_order_chart_legend,
)

SECOND_ORDER_KLINE_BARS = 250


DEFAULT_SECOND_ORDER_ROOT = Path(
    r"C:\Users\bai\Documents\我的文档\股票\新项目\SecondOrderGame"
)

# Tree roles for the material-cache news table: the message row stores its raw
# snippet under _NEWS_SNIPPET_ROLE; the inline original-text row is flagged
# with _NEWS_ORIGINAL_ROW_ROLE so clicking it again collapses the text box.
_NEWS_SNIPPET_ROLE = Qt.ItemDataRole.UserRole
_NEWS_ORIGINAL_ROW_ROLE = Qt.ItemDataRole.UserRole + 1


class _AnalysisResultPanel(QWidget):
    """Scan-friendly field cards with the complete payload available on demand."""

    def __init__(self, initial_text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._raw: object = initial_text
        self._plain_text = initial_text
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(0)
        self._title = QLabel()
        self._title.setStyleSheet("font-size: 17px; font-weight: 600;")
        header.addWidget(self._title)
        self._header_note = QLabel()
        self._header_note.setObjectName("mutedLabel")
        self._header_note.setStyleSheet(
            "color: #9AA5B1; font-size: 14px; margin-left: 30px;"
        )
        self._header_note.hide()
        header.addWidget(self._header_note)
        header.addStretch(1)
        self._raw_button = QPushButton("原始数据")
        self._raw_button.clicked.connect(self._show_raw)
        header.addWidget(self._raw_button)
        self._header_layout = header
        layout.addLayout(header)
        self._header_subtitle = QLabel()
        self._header_subtitle.setObjectName("mutedLabel")
        self._header_subtitle.setStyleSheet("color: #7F8A99; font-size: 13px;")
        self._header_subtitle.hide()
        layout.addWidget(self._header_subtitle)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cards = QWidget()
        self._cards_layout = QVBoxLayout(self._cards)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(10)
        self._cards_layout.addStretch(1)
        self._scroll.setWidget(self._cards)
        layout.addWidget(self._scroll, 1)
        self.setStyleSheet(
            "QFrame#secondOrderFieldCard { background: #11161D; border: 1px solid #2A3039; "
            "border-radius: 4px; } "
            "QLabel#secondOrderFieldName { background: transparent; border: none; "
            "color: #9AA5B1; font-size: 15px; font-weight: 600; } "
            "QLabel#secondOrderFieldValue { background: transparent; border: none; "
            "color: #E8ECF1; font-size: 15px; }"
        )
        self._render_cards({"状态": initial_text})

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_header_note(self, text: str) -> None:
        self._header_note.setText(text)
        self._header_note.setVisible(bool(text))

    def set_header_subtitle(self, text: str) -> None:
        self._header_subtitle.setText(text)
        self._header_subtitle.setVisible(bool(text))

    def add_header_action(self, button: QPushButton) -> None:
        self._header_layout.insertWidget(self._header_layout.count() - 1, button)
        self._header_layout.insertSpacing(self._header_layout.count() - 1, 8)

    def setPlainText(self, text: str) -> None:
        self._raw = text
        self._plain_text = text
        self._render_cards({"分析结果": text})

    def toPlainText(self) -> str:
        return self._plain_text

    def set_payload(self, summary: object, raw: object) -> None:
        self._raw = raw
        self._plain_text = self._format_value(summary)
        mapping = summary if isinstance(summary, Mapping) else {"分析结果": summary}
        self._render_cards(mapping)

    def set_grouped_payload(
        self,
        rows: list[list[tuple[str, object, int]]],
        raw: object,
    ) -> None:
        """Render semantically related fields in explicit horizontal rows."""
        self._raw = raw
        self._plain_text = self._format_value(
            {label: value for row in rows for label, value, _stretch in row}
        )
        self._clear_cards()
        for fields in rows:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)
            for label, value, stretch in fields:
                row_layout.addWidget(self._field_card(label, value), max(1, stretch))
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, row_widget)

    def set_table_sections(self, sections: list[Mapping[str, Any]]) -> None:
        """Append display-only table sections below the field cards.

        Each section mapping supports: ``title``, ``date`` (optional data-date
        note), ``metrics`` (optional list of (label, value) cards), ``headers``
        and ``rows`` (optional table), ``empty_text`` (fallback when no rows).
        Any later ``set_*`` call clears these sections together with the cards.
        """
        for section in sections:
            if not isinstance(section, Mapping):
                continue
            self._cards_layout.insertWidget(
                self._cards_layout.count() - 1, self._build_table_section(section)
            )

    def _build_table_section(self, section: Mapping[str, Any]) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)
        header = QHBoxLayout()
        title = QLabel(str(section.get("title") or "明细"))
        title.setStyleSheet("font-size: 14px; font-weight: 600; color: #E8ECF1;")
        header.addWidget(title)
        data_date = str(section.get("date") or "")
        if data_date:
            note = QLabel(f"数据截至 {data_date}")
            note.setStyleSheet("color: #7F8A99; font-size: 12px;")
            header.addWidget(note)
        header.addStretch(1)
        layout.addLayout(header)
        metrics = section.get("metrics") or []
        if metrics:
            metric_row = QHBoxLayout()
            metric_row.setSpacing(10)
            for label, value in metrics:
                metric_row.addWidget(self._field_card(str(label), value), 1)
            layout.addLayout(metric_row)
        headers = list(section.get("headers") or ())
        rows = list(section.get("rows") or ())
        if headers and rows:
            table = QTableWidget(len(rows), len(headers))
            table.setHorizontalHeaderLabels([str(header) for header in headers])
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            table.setAlternatingRowColors(True)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            table.setStyleSheet(
                "QTableWidget { background: #151920; border: 1px solid #2A3039; "
                "gridline-color: #232A33; color: #D5DBE3; } "
                "QHeaderView::section { background: #1A2028; color: #9AA5B1; "
                "border: none; padding: 5px; font-weight: 600; }"
            )
            for row_index, row in enumerate(rows):
                values = list(row) if isinstance(row, (list, tuple)) else [row]
                for column_index, value in enumerate(values[: len(headers)]):
                    item = QTableWidgetItem(self._format_value(value))
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    table.setItem(row_index, column_index, item)
            layout.addWidget(table)
        else:
            empty = QLabel(str(section.get("empty_text") or "暂无数据"))
            empty.setStyleSheet("color: #7F8A99; font-size: 13px; padding: 2px 0;")
            layout.addWidget(empty)
        return box

    def _render_cards(self, fields: Mapping[str, object]) -> None:
        self._clear_cards()
        for label, value in fields.items():
            self._cards_layout.insertWidget(
                self._cards_layout.count() - 1, self._field_card(str(label), value)
            )

    def _clear_cards(self) -> None:
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _field_card(self, label: str, value: object) -> QFrame:
        card = QFrame()
        card.setObjectName("secondOrderFieldCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 12)
        card_layout.setSpacing(7)
        name = QLabel(label)
        name.setObjectName("secondOrderFieldName")
        card_layout.addWidget(name)
        formatted = self._format_value(value)
        content = QLabel()
        if label == "新增买入" and "允许" in formatted:
            content.setStyleSheet("color: #63D391; font-weight: 600;")
        if label == "下一步" and formatted.startswith("应对树："):
            content.setText(f"<u>应对树</u>：{formatted.removeprefix('应对树：')}")
        else:
            content.setText(formatted)
        content.setObjectName("secondOrderFieldValue")
        content.setWordWrap(True)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        card_layout.addWidget(content)
        return card

    @classmethod
    def _format_value(cls, value: object, *, depth: int = 0) -> str:
        if value is None or value == "":
            return "暂无数据"
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, Mapping):
            lines = []
            for key, item in value.items():
                rendered = cls._format_value(item, depth=depth + 1)
                prefix = "  " * depth
                if "\n" in rendered:
                    rendered = rendered.replace("\n", "\n" + prefix + "  ")
                lines.append(f"{prefix}{key}：{rendered}")
            return "\n".join(lines) or "暂无数据"
        if isinstance(value, (list, tuple)):
            if not value:
                return "暂无数据"
            return "\n".join(
                f"• {cls._format_value(item, depth=depth + 1)}" for item in value
            )
        return str(value)

    def _show_raw(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{self._title.text()} - 原始数据")
        dialog.resize(900, 650)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            self._raw
            if isinstance(self._raw, str)
            else json.dumps(self._raw, ensure_ascii=False, indent=2, default=str)
        )
        layout.addWidget(text)
        dialog.exec()


class _LabelerStatusCard(QFrame):
    """Overview card rendering the OHLCV post-hoc labeler's two status fields.

    加载状态 (load) — whether the labeler's rules and labeling scope are ready.
    运行状态 (run)  — what the background catch-up thread is doing.

    Colors follow the workspace palette: gray = idle/skipped, blue =
    in-progress, green = ready/done, red = failed/unavailable.  The card is a
    pure renderer: it polls ``LabelerStatusTracker.snapshot()`` via the
    workspace's status timer, never owning the tracker.
    """

    _PILL_QSS = {
        "gray": "background: #202630; color: #9AA5B1; border: 1px solid #2A3039;",
        "blue": "background: #163B4A; color: #61C7E8; border: 1px solid #1E4A5E;",
        "green": "background: #193A2D; color: #63D391; border: 1px solid #21513B;",
        "red": "background: #4A2527; color: #F07B7B; border: 1px solid #693238;",
    }
    _LOAD_COLORS = {
        "not_loaded": "gray",
        "loading": "blue",
        "loaded": "green",
        "load_failed": "red",
    }
    _RUN_COLORS = {
        "idle": "gray",
        "running": "blue",
        "completed": "green",
        "partial_failure": "red",
        "skipped": "gray",
        "source_unavailable": "red",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("labelerStatusCard")
        self.setMaximumHeight(64)
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 7, 12, 7)
        root.setSpacing(14)
        title = QLabel("OHLCV 事后标注器状态")
        title.setStyleSheet("font-size: 14px; font-weight: 600; color: #E8ECF1;")
        root.addWidget(title)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet("color: #2A3039;")
        root.addWidget(divider)
        fields = QHBoxLayout()
        fields.setSpacing(16)
        self._load_pill = self._pill_field("加载状态")
        self._run_pill = self._pill_field("运行状态")
        fields.addLayout(self._load_pill)
        fields.addLayout(self._run_pill)
        root.addLayout(fields)
        root.addStretch(1)
        self._updated = QLabel("")
        self._updated.setStyleSheet("color: #7F8A99; font-size: 13px;")
        root.addWidget(self._updated)
        self._message = QLabel("等待标注器补跑线程启动。")
        self._message.hide()
        self.setStyleSheet(
            "QFrame#labelerStatusCard { background: #11161D; border: 1px solid #2A3039; "
            "border-radius: 4px; } "
            "QLabel#labelerStatusFieldName { background: transparent; border: none; "
            "color: #9AA5B1; font-size: 13px; font-weight: 600; } "
            "QLabel#labelerStatusPill { background: transparent; border: none; "
            "font-size: 14px; font-weight: 600; }"
        )
        self._render_idle()

    def _render_idle(self) -> None:
        self._set_pill(self._load_pill, "未加载", "gray")
        self._set_pill(self._run_pill, "空闲", "gray")
        self._message.setText("等待标注器补跑线程启动。")
        self._updated.setText("")
        self._message.setStyleSheet("color: #9AA5B1; font-size: 13px;")

    def set_status(self, status: object) -> None:
        """Render a LabelerStatus (or its to_dict mapping)."""
        if status is None:
            self._render_idle()
            return
        payload = status.to_dict() if hasattr(status, "to_dict") else dict(status)
        load_state = str(payload.get("load_state") or "not_loaded")
        run_state = str(payload.get("run_state") or "idle")
        load_label = str(payload.get("load_label") or load_state)
        run_label = str(payload.get("run_label") or run_state)
        self._set_pill(
            self._load_pill,
            load_label,
            self._LOAD_COLORS.get(load_state, "gray"),
        )
        self._set_pill(
            self._run_pill,
            run_label,
            self._RUN_COLORS.get(run_state, "gray"),
        )
        message = str(payload.get("run_message") or payload.get("load_message") or "")
        if message:
            self._message.setText(message)
            self.setToolTip(message)
        updated = str(payload.get("last_updated") or "")
        self._updated.setText(updated[11:19] if len(updated) >= 19 else updated)
        color = "#F07B7B" if run_state in {"partial_failure", "source_unavailable", "load_failed"} else "#9AA5B1"
        self._message.setStyleSheet(f"color: {color}; font-size: 13px;")

    @staticmethod
    def _pill_field(label: str) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(4)
        name = QLabel(label)
        name.setObjectName("labelerStatusFieldName")
        pill = QLabel("—")
        pill.setObjectName("labelerStatusPill")
        box.addWidget(name)
        box.addWidget(pill)
        return box

    def _set_pill(self, field: QVBoxLayout, text: str, color: str) -> None:
        pill = field.itemAt(1).widget()
        if isinstance(pill, QLabel):
            pill.setText(text)
            pill.setStyleSheet(
                f"background: transparent; border: none; font-size: 14px; "
                f"font-weight: 600; color: {self._pill_text_color(color)};"
            )

    @staticmethod
    def _pill_text_color(color: str) -> str:
        return {
            "gray": "#9AA5B1",
            "blue": "#61C7E8",
            "green": "#63D391",
            "red": "#F07B7B",
        }.get(color, "#9AA5B1")


class _CalibrationSummaryPanel(QFrame):
    """Compact, read-only Brier report for the history backtest tab."""

    _OUTCOME_LABELS = {
        "gap_down": "低于预期",
        "near_reference": "符合预期",
        "gap_up": "超预期强",
        "target_first": "先触及止盈",
        "stop_first": "先触及止损",
    }
    _DIRECTION_LABELS = {
        "increase": "增加该结果先验",
        "decrease": "减少该结果先验",
        "hold": "保持当前先验",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("secondOrderCalibrationPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(7)

        heading = QHBoxLayout()
        title = QLabel("概率校准（Brier）")
        title.setObjectName("secondOrderCalibrationTitle")
        heading.addWidget(title)
        heading.addStretch(1)
        note = QLabel("离线评估 · 不自动调参")
        note.setObjectName("secondOrderCalibrationNote")
        heading.addWidget(note)
        layout.addLayout(heading)

        self._status = QLabel("完成一次二阶分析后开始记录概率快照。")
        self._status.setObjectName("secondOrderCalibrationStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._table = QTableWidget(0, 5)
        self._table.setObjectName("secondOrderCalibrationTable")
        self._table.setHorizontalHeaderLabels(
            ["概率项", "决策点", "样本", "Brier", "先验建议"]
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.verticalHeader().hide()
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setVisible(False)
        layout.addWidget(self._table)

        self.setStyleSheet(
            "QFrame#secondOrderCalibrationPanel { background: #11161D; "
            "border: 1px solid #2A3039; border-radius: 4px; } "
            "QLabel#secondOrderCalibrationTitle { color: #E8ECF1; font-size: 15px; "
            "font-weight: 600; border: none; background: transparent; } "
            "QLabel#secondOrderCalibrationNote, QLabel#secondOrderCalibrationStatus { "
            "color: #9AA5B1; font-size: 13px; border: none; background: transparent; } "
            "QTableWidget#secondOrderCalibrationTable { background: #0D1218; "
            "alternate-background-color: #111820; border: 1px solid #252C35; "
            "gridline-color: #252C35; color: #D8DEE7; } "
            "QTableWidget#secondOrderCalibrationTable QHeaderView::section { "
            "background: #171D25; color: #9AA5B1; border: none; "
            "border-bottom: 1px solid #2A3039; padding: 5px 8px; font-weight: 600; }"
        )

    def set_summary(self, summary: Mapping[str, Any] | None) -> None:
        value = summary if isinstance(summary, Mapping) else {}
        reports = value.get("reports")
        reports = reports if isinstance(reports, list) else []
        predictions = int(value.get("prediction_count") or 0)
        resolved = int(value.get("resolved_prediction_count") or 0)
        minimum = int(value.get("minimum_sample_count") or 30)

        if predictions == 0:
            self._status.setText("完成一次二阶分析后开始记录概率快照。")
        else:
            self._status.setText(
                f"实际结果回填 {resolved} / {predictions}；"
                f"每个结果满 {minimum} 次后发布 Brier 分数和先验调整方向。"
            )

        self._table.setRowCount(len(reports))
        for row, report in enumerate(reports):
            report = report if isinstance(report, Mapping) else {}
            probability_type = str(report.get("probability_type") or "—")
            outcome = str(report.get("outcome") or "")
            outcome_label = self._OUTCOME_LABELS.get(outcome, outcome or "—")
            status = str(report.get("status") or "")
            brier = report.get("brier_score")
            direction = str(report.get("prior_adjustment_direction") or "")
            cells = (
                f"{probability_type} · {outcome_label}",
                str(report.get("decision_point") or "—"),
                str(int(report.get("sample_count") or 0)),
                f"{float(brier):.4f}"
                if status == "available" and isinstance(brier, (int, float))
                else "样本不足",
                self._DIRECTION_LABELS.get(direction, "待评估"),
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column in {1, 2, 3}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, column, item)
        self._table.setVisible(bool(reports))
        if reports:
            visible_rows = min(len(reports), 6)
            row_height = max(self._table.verticalHeader().defaultSectionSize(), 28)
            self._table.setFixedHeight(
                self._table.horizontalHeader().height() + visible_rows * row_height + 3
            )


class _AnalysisFlowCard(QFrame):
    """Zoomable horizontal view of the production analysis spine."""

    NODE_NAMES = (
        "构造技术分析-交接数据包",
        "检查分析设置",
        "准备分析材料",
        "消息预处理",
        "量化板块情绪",
        "量化博弈信号",
        "大模型推演",
        "B/C概率计算",
        "三情景应对",
        "T+1闸门",
        "分析结束",
    )
    _COLORS = {
        "pending": ("#202630", "#7F8A99", "待处理"),
        "active": ("#163B4A", "#61C7E8", "处理中"),
        "done": ("#193A2D", "#63D391", "已完成"),
        "error": ("#4A2527", "#F07B7B", "失败"),
        "stalled": ("#49351E", "#F2B66D", "处理卡顿"),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("analysisFlowCard")
        self._states = ["pending"] * len(self.NODE_NAMES)
        self._details = [""] * len(self.NODE_NAMES)
        self._active_index: int | None = None
        self._auto_fit = True
        self._stall_timer = QTimer(self)
        self._stall_timer.setSingleShot(True)
        self._stall_timer.timeout.connect(self._mark_active_stalled)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("分析架构主干流程")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        header.addWidget(title)
        self._flow_hint = QLabel("等待运行")
        self._flow_hint.setStyleSheet("color: #9AA5B1;")
        header.addWidget(self._flow_hint, 1)
        zoom_out = QPushButton("−")
        zoom_out.setToolTip("缩小流程图")
        zoom_out.setFixedWidth(30)
        zoom_out.clicked.connect(lambda: self._zoom(0.85))
        header.addWidget(zoom_out)
        zoom_in = QPushButton("+")
        zoom_in.setToolTip("放大流程图")
        zoom_in.setFixedWidth(30)
        zoom_in.clicked.connect(lambda: self._zoom(1.18))
        header.addWidget(zoom_in)
        fit = QPushButton("适应")
        fit.setToolTip("让流程图适应当前容器")
        fit.clicked.connect(self.fit_to_view)
        header.addWidget(fit)
        root.addLayout(header)

        self._view = QGraphicsView()
        self._view.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        self._view.setMinimumHeight(168)
        self._scene = QGraphicsScene(self._view)
        self._view.setScene(self._scene)
        root.addWidget(self._view)

        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(
            "color: #F07B7B; background: #301E22; border: 1px solid #693238; "
            "padding: 6px 8px;"
        )
        self._error_label.hide()
        root.addWidget(self._error_label)
        self.setStyleSheet(
            "QFrame#analysisFlowCard { background: #11161D; border: 1px solid #2A3039; "
            "border-radius: 4px; }"
        )
        self._rebuild_scene()

    @property
    def active_index(self) -> int | None:
        return self._active_index

    def reset(self) -> None:
        self._stall_timer.stop()
        self._states = ["pending"] * len(self.NODE_NAMES)
        self._details = [""] * len(self.NODE_NAMES)
        self._active_index = None
        self._error_label.hide()
        self._flow_hint.setText("等待运行")
        self._rebuild_scene()

    def set_status(self, index: int, status: str, detail: str = "") -> None:
        if not 0 <= index < len(self.NODE_NAMES):
            return
        self._states[index] = status if status in self._COLORS else "pending"
        self._details[index] = detail
        if status == "active":
            self._active_index = index
            self._stall_timer.start(30_000)
            self._flow_hint.setText(f"处理中：{self.NODE_NAMES[index]}")
        elif self._active_index == index:
            self._stall_timer.stop()
            if status in {"done", "error", "stalled"}:
                self._active_index = None
            self._flow_hint.setText(
                f"{self._COLORS[status][2]}：{self.NODE_NAMES[index]}"
            )
        if status in {"error", "stalled"}:
            self._show_error(index, detail or self._COLORS[status][2])
        self._rebuild_scene()

    def _show_error(self, index: int, message: str) -> None:
        self._error_label.setText(
            f"异常节点：{self.NODE_NAMES[index]}\n{message}"
        )
        self._error_label.show()

    def _mark_active_stalled(self) -> None:
        if self._active_index is not None:
            self.set_status(
                self._active_index,
                "stalled",
                "超过 30 秒未收到阶段进展或模型输出，请检查后台任务或重试。",
            )

    def _zoom(self, factor: float) -> None:
        self._auto_fit = False
        self._view.scale(factor, factor)

    def fit_to_view(self) -> None:
        self._auto_fit = True
        self._view.resetTransform()
        self._fit_scene()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._auto_fit:
            QTimer.singleShot(0, self._fit_scene)

    def _fit_scene(self) -> None:
        if self._scene.itemsBoundingRect().isNull():
            return
        # Nodes stay at their authored pixel size on every screen. Narrow
        # viewports scroll horizontally instead of shrinking the cards.
        self._view.resetTransform()
        self._view.centerOn(self._scene.itemsBoundingRect().center())

    def _rebuild_scene(self) -> None:
        self._scene.clear()
        node_width, node_height = 120.0, 78.0
        gap = 24.0
        top = 24.0
        for index, name in enumerate(self.NODE_NAMES):
            left = index * (node_width + gap)
            state = self._states[index]
            fill, accent, status_text = self._COLORS[state]
            rect = self._scene.addRect(
                left, top, node_width, node_height,
                QPen(QColor(accent), 1.4), QBrush(QColor(fill))
            )
            rect.setZValue(1)
            label = self._scene.addText(name)
            label.setDefaultTextColor(QColor("#E8ECF1"))
            label.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.DemiBold))
            label.setTextWidth(node_width - 12)
            label.setPos(left + 6, top + 8)
            label.setZValue(2)
            status = self._scene.addText(
                f"{status_text}" + (f"\n{self._details[index]}" if self._details[index] else "")
            )
            status.setDefaultTextColor(QColor(accent))
            status.setFont(QFont("Microsoft YaHei", 9))
            status.setTextWidth(node_width - 12)
            status.setPos(left + 6, top + 48)
            status.setZValue(2)
            if index < len(self.NODE_NAMES) - 1:
                connector = self._scene.addLine(
                    left + node_width, top + node_height / 2,
                    left + node_width + gap, top + node_height / 2,
                    QPen(QColor("#596474"), 1.2)
                )
                connector.setZValue(0)
        total_width = len(self.NODE_NAMES) * node_width + (len(self.NODE_NAMES) - 1) * gap
        self._scene.setSceneRect(0, 0, total_width, top + node_height + 24)
        if self._auto_fit:
            QTimer.singleShot(0, self._fit_scene)


def second_order_root() -> Path:
    return Path(os.environ.get("SECOND_ORDER_GAME_ROOT", str(DEFAULT_SECOND_ORDER_ROOT))).resolve()


def _market_raw_projection(market: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the DSA display sections as the single raw-analysis body."""
    keys = (
        "status",
        "source",
        "created_at",
        "data_date",
        "decision_date",
        "age_days",
        "usable_for_analysis",
        "reason",
        "display_sections",
    )
    return {key: market[key] for key in keys if key in market}


def _format_published_date_text(value: object) -> str:
    """Render provider timestamps (RFC 2822 / ISO) as compact local strings."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            parsed = None
    if parsed is None:
        return text
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _sentiment_index_display(value: object, details: Mapping[str, Any]) -> object:
    status = str(details.get("status") or "")
    if status == "market_data_unavailable":
        return f"数据源不可用（保持值 {value}）"
    if status == "non_trading_day":
        return f"休市（保持值 {value}）"
    return value


_LABELER_STATUS_TRACKER: Any = None


def _pa_settings_path() -> str | None:
    """Absolute path to PA's settings.json (second_order.symbol_preferences).

    Drives the labeler's watchlist mode: each watchlist symbol is benchmarked
    by its associated sector index from ``symbol_preferences``.  Returns None
    when the file is absent so the backend falls back to the material-cache
    sector registry (and reports an empty scope instead of crashing).
    """
    candidate = Path(__file__).resolve().parents[2] / "config" / "settings.json"
    return str(candidate) if candidate.is_file() else None


def _shared_labeler_status_tracker() -> Any:
    """One process-wide labeler-status tracker shared by every workspace.

    The labeler catch-up scope is market-wide (watchlist + registered
    sectors), independent of the instrument page, so a single shared tracker
    keeps the overview cards consistent and lets the backend's
    ``mark_running`` guard deduplicate concurrent sweeps.
    """
    global _LABELER_STATUS_TRACKER
    if _LABELER_STATUS_TRACKER is None:
        root_text = str(second_order_root())
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        from src.integration.labeler_status import LabelerStatusTracker

        _LABELER_STATUS_TRACKER = LabelerStatusTracker()
    return _LABELER_STATUS_TRACKER


def _load_second_order_modules():
    root = second_order_root()
    if not (root / "src" / "integration" / "pa_embedded_service.py").is_file():
        raise FileNotFoundError(f"SecondOrderGame 项目不存在：{root}")
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from src.integration import PAChatClientAdapter, PAEmbeddedService, PAMarketDataAdapter

    return PAChatClientAdapter, PAEmbeddedService, PAMarketDataAdapter


def _create_market_source(context: Any, symbol: str):
    if context is None or getattr(context, "settings", None) is None:
        raise RuntimeError("PA 设置未初始化")
    kind = str(
        getattr(
            getattr(context.settings, "second_order", None),
            "market_data_source",
            "futu",
        )
        or "futu"
    )
    if kind == "akshare":
        from pa_agent.data.akshare_source import AkShareSource

        source = AkShareSource()
    else:
        from pa_agent.data.futu_source import FutuSource

        source = FutuSource(context.settings)
    try:
        source.connect()
        source.subscribe(symbol, "2h")
    except Exception:
        source.disconnect()
        raise
    return source


def _refresh_dsa_market_cache(context: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read the latest same-day DSA cache without starting DSA."""
    root = second_order_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.integration.dsa_market_context import load_latest_dsa_market_context

    configured = str(payload.get("dsa_database_path") or "").strip()
    if configured:
        return load_latest_dsa_market_context(configured, max_age_days=0)
    return load_latest_dsa_market_context(max_age_days=0)


def _run_dsa_market_review(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Force a user-requested DSA market review and return its persisted result."""
    root = second_order_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.integration.dsa_runtime import ensure_current_dsa_market_review

    return ensure_current_dsa_market_review(payload, force=True)


def _embedded_service(
    context: Any,
    market_source: Any,
    *,
    model_activity_callback: Any | None = None,
    on_progress_event: Any | None = None,
):
    settings = getattr(context, "settings", None)
    provider = getattr(settings, "provider", None)
    if provider is None:
        raise RuntimeError("PA 大模型配置未初始化")
    # Reuse PA's provider implementation and settings, but create a fresh
    # client for this run.  No PA analysis/free-chat session object crosses
    # into SecondOrderGame; this workspace owns its explicit conversation.
    from pa_agent.ai.client_factory import create_ai_client

    isolated_client = create_ai_client(provider, logger_=getattr(context, "logger", None))
    PAChatClientAdapter, PAEmbeddedService, PAMarketDataAdapter = _load_second_order_modules()
    from src.integration.progress import ProgressEvent, ProgressSink

    sink = ProgressSink()
    if on_progress_event is not None:
        sink.subscribe(on_progress_event)

    def _token_callback(kind: str, chunk: str) -> None:
        sink.emit(
            ProgressEvent(
                ts=datetime.now(),
                kind=kind,
                stage="",
                message=chunk,
                source="model",
            )
        )

    sector_market_source = _create_sector_market_source(settings, market_source)
    fallback = None
    tavily_key = str(
        getattr(getattr(settings, "second_order", None), "tavily_api_key", "") or ""
    ).strip()
    if tavily_key:
        from src.data import TavilyNewsProvider

        fallback = TavilyNewsProvider(tavily_key)
    try:
        max_news_items = int(
            getattr(getattr(settings, "second_order", None), "max_news_items", 18) or 18
        )
    except (TypeError, ValueError):
        max_news_items = 18
    max_news_items = max(5, min(30, max_news_items))
    return PAEmbeddedService(
        market_source=PAMarketDataAdapter(
            market_source,
            news_fallback_provider=fallback,
            sector_market_source=sector_market_source,
            max_bars=SECOND_ORDER_KLINE_BARS,
            max_news_items=max_news_items,
        ),
        model_client=PAChatClientAdapter(
            isolated_client,
            provider="PA_Agent.second_order",
            activity_callback=model_activity_callback,
            token_callback=_token_callback,
        ),
        progress_sink=sink,
        labeler_status_tracker=_shared_labeler_status_tracker(),
        pa_settings_path=_pa_settings_path(),
        dsa_runtime_enabled=True,
    )


def _create_sector_market_source(settings: Any, market_source: Any):
    root_text = str(second_order_root())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from src.data import AkShareMarketDataSource, FutuMarketDataSource

    # 龙虎榜与连板池都由 AkShare 提供（富途 OpenD 无这两个数据源）；akshare 延迟
    # import，未安装时在取数阶段降级为 source_error，不阻塞板块/个股行情。
    akshare_source = AkShareMarketDataSource()

    quote_context = getattr(market_source, "_context", None)
    # 仅当 _context 是真正的 OpenQuoteContext（暴露 get_plate_stock）才复用；
    # 否则（未连接 / 属性缺失 / 传入了错误对象）回退 host/port 自建连接，避免把
    # 无效对象注入 FutuMarketDataSource 导致运行时报 "no attribute ..."。
    if quote_context is not None and callable(getattr(quote_context, "get_plate_stock", None)):
        return FutuMarketDataSource(
            quote_context=quote_context,
            dragon_tiger_provider=akshare_source,
            breadth_provider=akshare_source,
        )
    futu = getattr(settings, "futu", None)
    return FutuMarketDataSource(
        host=str(getattr(futu, "opend_host", "127.0.0.1") or "127.0.0.1"),
        port=int(getattr(futu, "opend_port", 11111) or 11111),
        dragon_tiger_provider=akshare_source,
        breadth_provider=akshare_source,
    )


def _to_frame(result: Mapping[str, Any]) -> KlineFrame:
    raw_bars = list(result.get("bars") or ())
    if not raw_bars:
        raise ValueError("Futu OpenD 没有返回 K 线")
    newest_first: list[KlineBar] = []
    for seq, row in enumerate(reversed(raw_bars), 1):
        timestamp = datetime.fromisoformat(str(row["time"]))
        newest_first.append(
            KlineBar(
                seq=seq,
                ts_open=timestamp.timestamp() * 1000,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                amount=float(row.get("turnover") or 0),
                closed=True,
                timestamp_is_close=True,
            )
        )
    return KlineFrame(
        symbol=str(result.get("symbol") or ""),
        timeframe="2h",
        bars=tuple(newest_first),
        indicators=compute_indicators(newest_first),
        snapshot_ts_local_ms=int(datetime.now().timestamp() * 1000),
    )


def _kline_chart_payload(source: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Fetch one closed-bar history and derive the chart-only signal series."""
    root = second_order_root()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from src.integration import PAMarketDataAdapter
    from src.probability.models import DecisionPoint
    from src.signals import (
        GameSignalCalculator,
        GameSignalRequest,
        load_game_signal_config,
    )

    symbol = str(payload.get("symbol") or "").strip()
    subscribed_symbol = str(getattr(source, "_symbol", "") or symbol).strip()
    adapter = PAMarketDataAdapter(source, max_bars=SECOND_ORDER_KLINE_BARS)
    bars = adapter.get_kline(
        subscribed_symbol,
        "K_120M",
        "1970-01-01",
        "2999-12-31",
    )
    if not bars:
        raise ValueError("Futu OpenD 没有返回 K 线")
    decision_value = str(payload.get("decision_point") or "").strip()
    decision_point = (
        DecisionPoint.MIDDAY
        if decision_value in {"midday", "午盘"}
        else DecisionPoint.CLOSE
    )
    request = GameSignalRequest(
        code=subscribed_symbol,
        start=bars[0].time_key[:10],
        end=bars[-1].time_key[:10],
        decision_point=decision_point,
    )
    calculator = GameSignalCalculator(
        adapter,
        load_game_signal_config(root / "config" / "signals.yaml"),
    )
    signal_series = calculator.calculate_series_from_bars(
        request,
        bars,
        display_bars=len(bars),
    )
    return {
        "ok": True,
        "symbol": symbol,
        "timeframe": "120m",
        "bars": [
            {
                "time": bar.time_key,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "turnover": bar.turnover,
            }
            for bar in bars
        ],
        "game_signal_series": [point.to_dict() for point in signal_series],
    }


class _ApiWorker(QThread):
    succeeded = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)
    progress = pyqtSignal(str, str, str)
    progress_event = pyqtSignal(object)

    def __init__(self, context: Any, operation: str, payload: Mapping[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.context = context
        self.operation = operation
        self.payload = dict(payload)
        self._model_activity_stage: str | None = None

    def _model_activity(self) -> None:
        """Refresh the UI watchdog when the streamed model emits a token."""
        if self._model_activity_stage is not None:
            self.progress.emit(self.operation, self._model_activity_stage, "active")

    def _on_progress_event(self, event: Any) -> None:
        """Forward a backend ProgressEvent to the UI thread as a plain dict."""
        self.progress_event.emit(event.to_dict())

    def _with_dsa_heartbeat(self, service: Any, analysis_payload: Mapping[str, Any]) -> object:
        """Run DSA market-material generation with a UI keep-alive heartbeat.

        The first run of a trading half-day window launches the external DSA
        backend synchronously (cold start + full market review), which routinely
        exceeds the frontend's 30s stall watchdog. Re-emitting the ``settings``
        stage as ``active`` every 20s resets that watchdog so the run is not
        falsely reported as timed out.
        """
        stop = Event()

        def _beat() -> None:
            while not stop.wait(20):
                self.progress.emit("analysis", "settings", "active")

        thread = Thread(target=_beat, daemon=True)
        thread.start()
        try:
            return service.ensure_market_material(analysis_payload)
        finally:
            stop.set()
            thread.join(timeout=2)

    def run(self) -> None:
        source = None
        service = None
        try:
            if self.operation == "market_refresh":
                self.progress.emit("market_refresh", "market", "active")
                value = _run_dsa_market_review(self.payload)
                self.progress.emit("market_refresh", "market", "done")
                self.succeeded.emit(self.operation, value)
                return
            if self.operation == "labeler_catchup":
                # 构造 service 即触发标注器补跑；返回结构化状态供概览卡片渲染。
                # PAMarketDataAdapter 构造时强制要求数据源暴露 latest_snapshot(n)，
                # 因此这里必须提供真实行情源；传 None 会直接抛
                # "PA data source must expose latest_snapshot(n)"。
                symbol = str(self.payload.get("symbol") or "").strip()
                source = _create_market_source(self.context, symbol)
                service = _embedded_service(self.context, source)
                value = service.labeler_status().to_dict()
                self.succeeded.emit(self.operation, value)
                return
            source = _create_market_source(
                self.context, str(self.payload.get("symbol") or "").strip()
            )
            if self.operation == "kline":
                value = _kline_chart_payload(source, self.payload)
            elif self.operation == "analysis":
                service = _embedded_service(
                    self.context,
                    source,
                    model_activity_callback=self._model_activity,
                    on_progress_event=self._on_progress_event,
                )
                analysis_payload = dict(self.payload)
                self.progress.emit("analysis", "handoff", "done")
                self.progress.emit("analysis", "settings", "active")
                dsa_result = self._with_dsa_heartbeat(service, analysis_payload)
                market_material = (
                    dsa_result.get("market") if isinstance(dsa_result, Mapping) else None
                )
                if (
                    not isinstance(dsa_result, Mapping)
                    or dsa_result.get("status") != "ready"
                    or not isinstance(market_material, Mapping)
                ):
                    raise RuntimeError("DSA 大盘材料未就绪，不能进入正式二阶推演")
                analysis_payload["market_analysis"] = dict(market_material)
                self.progress.emit("analysis", "settings", "done")
                self.progress.emit("analysis", "materials", "active")
                sector = str(
                    analysis_payload.get("sector_name")
                    or analysis_payload.get("stock_name")
                    or analysis_payload.get("symbol")
                    or ""
                ).strip()
                keyword = sector
                if not sector or not keyword:
                    raise RuntimeError("缺少板块名称，不能执行正式推演前的消息预取")
                prefetch_kwargs = {"search_keywords": {sector: keyword}}
                sector_code = str(analysis_payload.get("sector_code") or "").strip()
                if sector_code:
                    prefetch_kwargs["sector_codes"] = {sector: sector_code}
                self.progress.emit("analysis", "messages", "active")
                prefetched = service.prefetch_news((sector,), **prefetch_kwargs)
                if not isinstance(prefetched, Mapping) or not prefetched.get("ok"):
                    reason = (
                        prefetched.get("error")
                        if isinstance(prefetched, Mapping)
                        else "消息预取未返回有效结果"
                    )
                    raise RuntimeError(f"正式推演前消息预取失败：{reason}")
                self.progress.emit("analysis", "messages", "done")
                self.progress.emit("analysis", "sentiment", "active")
                self.progress.emit("analysis", "signals", "active")
                self._model_activity_stage = "materials"
                prepared = service.prepare_materials(analysis_payload)
                if not prepared.get("ok"):
                    raise RuntimeError(
                        "运行前材料预分析失败："
                        + str(prepared.get("error") or prepared.get("status"))
                    )
                self.progress.emit("analysis", "sentiment", "done")
                self.progress.emit("analysis", "signals", "done")
                self.progress.emit("analysis", "materials", "done")
                self._model_activity_stage = "model"
                self.progress.emit("analysis", "model", "active")
                self.progress.emit("analysis", "probabilities", "active")
                self.progress.emit("analysis", "scenarios", "active")
                self.progress.emit("analysis", "gate", "active")
                value = service.run_analysis(analysis_payload)
                for stage in ("model", "probabilities", "scenarios", "gate"):
                    self.progress.emit("analysis", stage, "done")
                self.progress.emit("analysis", "finish", "done")
                self._model_activity_stage = None
                # 板块分析展示数据（资金流/连板/龙虎榜）与推演解耦：不进推演材料，
                # 仅在结果上附加，供「板块分析」页渲染，失败不阻塞主结果。
                value = _attach_sector_analysis_bundle(service, value, sector, sector_code)
            elif self.operation == "news_prefetch":
                self.progress.emit("news_prefetch", "messages", "active")
                sector = str(
                    self.payload.get("sector_name")
                    or self.payload.get("stock_name")
                    or self.payload.get("symbol")
                    or ""
                ).strip()
                keyword = sector
                prefetch_kwargs = {"search_keywords": {sector: keyword}}
                sector_code = str(self.payload.get("sector_code") or "").strip()
                if sector_code:
                    prefetch_kwargs["sector_codes"] = {sector: sector_code}
                service = _embedded_service(self.context, source)
                value = service.prefetch_news(
                    (sector,), **prefetch_kwargs
                )
                self.progress.emit("news_prefetch", "messages", "done")
            elif self.operation == "material_preanalysis":
                self.progress.emit("material_preanalysis", "materials", "active")
                service = _embedded_service(self.context, source)
                value = service.prepare_materials(self.payload)
                if isinstance(value, Mapping) and value.get("ok"):
                    for stage in ("sentiment", "signals", "materials"):
                        self.progress.emit("material_preanalysis", stage, "done")
            else:
                raise ValueError(f"unknown operation: {self.operation}")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.operation, str(exc) or type(exc).__name__)
        else:
            self.succeeded.emit(self.operation, value)
        finally:
            try:
                closer = getattr(service, "close", None)
                if callable(closer):
                    closer()
            finally:
                if source is not None:
                    source.disconnect()


def _attach_sector_analysis_bundle(
    service: Any,
    value: object,
    sector_name: str,
    sector_code: str,
) -> object:
    """Attach the display-only 板块分析 bundle to a finished analysis result.

    The bundle (资金流向 / 连板信息 / 龙虎榜) is collected after the reasoning
    pipeline completes and never feeds it; any failure keeps the main result
    intact and simply omits the bundle key.
    """
    if not isinstance(value, Mapping) or not str(sector_code or "").strip():
        return value
    try:
        bundle = service.collect_sector_analysis(
            sector_code=str(sector_code).strip(),
            sector_name=str(sector_name or ""),
        )
    except Exception:  # noqa: BLE001 — display data must not break the analysis
        return value
    if isinstance(bundle, Mapping):
        return {**value, "sector_analysis_bundle": bundle}
    return value


def _fmt_amount(value: object) -> str:
    """Render an amount in 元 compactly: 亿 / 万 / raw, or '—' when absent."""
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(number) >= 1e8:
        return f"{number / 1e8:.2f} 亿"
    if abs(number) >= 1e4:
        return f"{number / 1e4:,.0f} 万"
    return f"{number:,.0f}"


def _fmt_wan(value: object) -> str:
    """Render a 资金流 amount whose input unit is 万元 (Futu capital flow)."""
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(number) >= 1e4:  # ≥ 1 亿元
        return f"{number / 1e4:.2f} 亿"
    return f"{number:,.0f} 万"


def _signed_net(buy: object, sell: object) -> float | None:
    if buy is None and sell is None:
        return None
    return float(buy or 0) - float(sell or 0)


def _sector_bundle_sections(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Shape the 板块分析 bundle into renderable table sections."""
    errors = [str(item) for item in (bundle.get("errors") or ())][:2]
    err_note = "；".join(errors)
    data_date = str(bundle.get("date") or "")
    flows = list(bundle.get("capital_flow") or ())
    pool = list(bundle.get("limit_pool") or ())
    tiger = list(bundle.get("dragon_tiger") or ())
    sections: list[dict[str, Any]] = []

    if flows:
        latest = flows[-1]
        sections.append(
            {
                "title": "资金流向",
                "date": data_date,
                "metrics": [
                    ("主力净流入（最近交易日）", _fmt_wan(latest.get("main_in_flow"))),
                    ("超大单", _fmt_wan(latest.get("super_in_flow"))),
                    ("大单", _fmt_wan(latest.get("big_in_flow"))),
                ],
                "headers": ["日期", "主力净流入", "超大单", "大单", "中单", "小单"],
                "rows": [
                    [
                        str(item.get("date") or ""),
                        _fmt_wan(item.get("main_in_flow")),
                        _fmt_wan(item.get("super_in_flow")),
                        _fmt_wan(item.get("big_in_flow")),
                        _fmt_wan(item.get("mid_in_flow")),
                        _fmt_wan(item.get("sml_in_flow")),
                    ]
                    for item in flows
                ],
                "empty_text": "台账中无该板块资金流记录",
            }
        )
    else:
        sections.append(
            {"title": "资金流向", "date": data_date, "empty_text": "台账中无该板块资金流记录"}
        )

    if pool:
        rise_streaks = [
            int(item["limit_streak"])
            for item in pool
            if item.get("direction") == "rise" and item.get("limit_streak")
        ]
        rise_count = sum(1 for item in pool if item.get("direction") == "rise")
        fall_count = sum(1 for item in pool if item.get("direction") == "fall")
        sections.append(
            {
                "title": "连板信息",
                "date": data_date,
                "metrics": [
                    ("最高连板", f"{max(rise_streaks)} 板" if rise_streaks else "—"),
                    ("涨停股数", rise_count),
                    ("跌停股数", fall_count),
                ],
                "headers": ["代码", "连板数", "方向"],
                "rows": [
                    [
                        str(item.get("code") or ""),
                        f"{int(item['limit_streak'])} 板",
                        "涨停" if item.get("direction") == "rise" else "跌停",
                    ]
                    for item in pool
                ],
                "empty_text": "当日及前 3 日无板块成分股涨停/跌停记录",
            }
        )
    else:
        sections.append(
            {
                "title": "连板信息",
                "date": data_date,
                "empty_text": "当日及前 3 日无板块成分股涨停/跌停记录",
            }
        )

    if tiger:
        inst_net = sum(
            (item.get("institution_net_buy") or 0) - (item.get("institution_net_sell") or 0)
            for item in tiger
        )
        hot_net = sum(
            (item.get("hot_money_net_buy") or 0) - (item.get("hot_money_net_sell") or 0)
            for item in tiger
        )
        sections.append(
            {
                "title": "龙虎榜",
                "date": data_date,
                "metrics": [
                    ("上榜股数", len(tiger)),
                    ("机构净买", _fmt_amount(inst_net)),
                    ("游资净买", _fmt_amount(hot_net)),
                ],
                "headers": ["代码", "上榜原因", "净买入", "机构净买", "游资净买", "机构席位", "游资席位"],
                "rows": [
                    [
                        str(item.get("code") or ""),
                        str(item.get("reason") or ""),
                        _fmt_amount(item.get("net_buy_amount")),
                        _fmt_amount(
                            _signed_net(item.get("institution_net_buy"), item.get("institution_net_sell"))
                        ),
                        _fmt_amount(
                            _signed_net(item.get("hot_money_net_buy"), item.get("hot_money_net_sell"))
                        ),
                        "、".join(item.get("institution_seats") or ()) or "—",
                        "、".join(item.get("hot_money_seats") or ()) or "—",
                    ]
                    for item in tiger
                ],
                "empty_text": "当日及前 3 日无板块成分股上榜",
            }
        )
    else:
        sections.append(
            {"title": "龙虎榜", "date": data_date, "empty_text": "当日及前 3 日无板块成分股上榜"}
        )

    if err_note:
        note = f"（数据源错误：{err_note[:60]}）"
        for section in sections:
            if not section.get("rows"):
                section["empty_text"] = f"{section.get('empty_text') or '暂无数据'}{note}"
                break
        else:
            # 三个分区都有数据时，错误提示挂在第一个分区的日期注记上
            sections[0]["date"] = f"{sections[0].get('date') or ''} {note}".strip()
    return sections


def _progress_transcript(events: list[Mapping[str, Any]]) -> list[str]:
    """Flatten a progress-event sequence into readable display lines."""
    lines: list[str] = []
    pending = ""
    for event in events:
        if not isinstance(event, Mapping):
            continue
        kind = str(event.get("kind") or "")
        message = str(event.get("message") or "")
        if kind in {"thinking", "content"}:
            pending += message
            continue
        if pending:
            lines.append(pending)
            pending = ""
        if kind == "stage":
            lines.append(f"▶ {message}")
        elif kind == "error":
            lines.append(f"✗ {message}")
        elif kind == "info":
            lines.append(f"· {message}")
    if pending:
        lines.append(pending)
    return lines


class _StreamingProgressView(QWidget):
    """Expanded-by-default live transcript of the second-order reasoning run."""

    _COLLAPSED_LINES = 3
    _COLLAPSED_HEIGHT = 76
    _EXPANDED_HEIGHT = 280
    _BUFFER_LINES = 2000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._blocks: list[str] = []
        self._pending = ""
        self._collapsed = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("实时推演")
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch(1)
        self._toggle = QPushButton("展开")
        self._toggle.setFixedWidth(56)
        self._toggle.clicked.connect(self._toggle_expanded)
        header.addWidget(self._toggle)
        layout.addLayout(header)
        self._edit = QPlainTextEdit()
        self._edit.setReadOnly(True)
        self._edit.setFrameShape(QFrame.Shape.NoFrame)
        self._edit.setFont(QFont("Cascadia Mono", 10))
        self._edit.setStyleSheet(
            "QPlainTextEdit { background: #0F141B; border: 1px solid #2A3039; "
            "border-radius: 4px; color: #C6CDD6; }"
        )
        layout.addWidget(self._edit)
        self._edit.setFixedHeight(self._COLLAPSED_HEIGHT)
        self._edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._refresh()

    def _toggle_expanded(self) -> None:
        self._collapsed = not self._collapsed
        self._refresh()

    def append_event(self, event: Mapping[str, Any]) -> None:
        kind = str(event.get("kind") or "")
        message = str(event.get("message") or "")
        if kind in {"thinking", "content"}:
            self._pending += message
        elif kind == "stage":
            self._flush_pending()
            self._blocks.append(f"▶ {message}")
        elif kind == "error":
            self._flush_pending()
            self._blocks.append(f"✗ {message}")
        elif kind == "info":
            self._flush_pending()
            self._blocks.append(f"· {message}")
        self._trim()
        self._refresh()

    def set_replay(self, events: list[Mapping[str, Any]]) -> None:
        self.clear()
        self._blocks = _progress_transcript(events)
        self._collapsed = False
        self._refresh()

    def _flush_pending(self) -> None:
        if self._pending:
            self._blocks.append(self._pending)
            self._pending = ""

    def _trim(self) -> None:
        if len(self._blocks) > self._BUFFER_LINES:
            self._blocks = self._blocks[-self._BUFFER_LINES:]

    def _visible_blocks(self) -> list[str]:
        blocks = list(self._blocks)
        if self._pending:
            blocks.append(self._pending)
        return blocks

    def _refresh(self) -> None:
        blocks = self._visible_blocks()
        if self._collapsed:
            tail = blocks[-self._COLLAPSED_LINES:]
            text = ("…\n" if len(blocks) > self._COLLAPSED_LINES else "") + "\n".join(tail)
            self._edit.setPlainText(text)
            self._edit.setFixedHeight(self._COLLAPSED_HEIGHT)
            self._edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._toggle.setText("展开")
        else:
            self._edit.setPlainText("\n".join(blocks))
            self._edit.setFixedHeight(self._EXPANDED_HEIGHT)
            self._edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self._toggle.setText("收起")
        self._edit.moveCursor(QTextCursor.MoveOperation.End)

    def clear(self) -> None:
        self._blocks = []
        self._pending = ""
        self._collapsed = False
        self._refresh()


class SecondOrderWorkspace(QWidget):
    """Native PA page with a conversation isolated from every PA module."""

    model_settings_requested = pyqtSignal()
    pipeline_step_changed = pyqtSignal(int, str)
    pipeline_reset_requested = pyqtSignal()
    _FLOW_STAGE_INDEX = {
        "handoff": 0,
        "settings": 1,
        "materials": 2,
        "messages": 3,
        "sentiment": 4,
        "signals": 5,
        "model": 6,
        "probabilities": 7,
        "scenarios": 8,
        "gate": 9,
        "finish": 10,
    }

    def __init__(self, pa_context: Any = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pa_context = pa_context
        self._pa_settings = getattr(pa_context, "settings", None)
        self._payload: dict[str, Any] = {}
        self._last_result_envelope: dict[str, Any] | None = None
        self._chat_context: list[dict[str, str]] = []
        from pa_agent.records.prompt_library import PromptLibraryStore

        self._chat_prompt_library = PromptLibraryStore()
        self._chat_prompt_usage: dict[str, int] = {}
        self._workers: set[_ApiWorker] = set()
        self._pending_analysis = False
        self._last_symbol = ""
        self._building_ui = True
        self._build_ui()
        self._building_ui = False
        self._labeler_catchup_requested = False
        self._labeler_status_timer = QTimer(self)
        self._labeler_status_timer.timeout.connect(self._refresh_labeler_status)
        self._labeler_status_timer.start(1000)
        settings = getattr(self._pa_settings, "general", None)
        initial_symbol = str(getattr(settings, "last_symbol", "") or "").strip()
        if not initial_symbol:
            data_source = getattr(pa_context, "data_source", None)
            initial_symbol = str(getattr(data_source, "_symbol", "") or "").strip()
        if initial_symbol:
            self.set_symbol(initial_symbol)
        QTimer.singleShot(0, self._refresh_dsa_on_open)

    def timeframe(self) -> str:
        return "120m"

    def analysis_tabs(self) -> tuple[str, ...]:
        """Return the labels in the primary analysis navigation.

        The two compound areas (博弈推演 and 应对方案) intentionally expose
        their related views through an inner tab bar.  Callers that need to
        inspect those views can use ``_game_tabs``/``_response_tabs``; the
        public summary reflects the navigation users see at the top level.
        """
        return tuple(self._tabs.tabText(index) for index in range(self._tabs.count()))

    def set_symbol(self, symbol: str) -> None:
        value = str(symbol or "").strip()
        if value:
            if self._last_symbol and value != self._last_symbol:
                self._chat_context.clear()
                self._chat_transcript.setPlainText(
                    f"品种已切换为 {value}，二阶独立对话上下文已清空。"
                )
            self._last_symbol = value
            self._symbol.setText(value)
            self._load_symbol_preferences(value)
            if (
                not self._building_ui
                and self._tabs.currentWidget() is self._history
            ):
                QTimer.singleShot(0, self._refresh_history)

    def _pa_analysis_complete(self) -> bool:
        """两个阶段（stage1 诊断 + stage2 决策）都拿到才算完成 PA 技术分析。"""
        return isinstance(self._payload.get("stage1_diagnosis"), Mapping) and isinstance(
            self._payload.get("stage2_decision"), Mapping
        )

    def _update_run_tooltip(self) -> None:
        if not hasattr(self, "_run"):
            return
        self._run.setToolTip(
            "" if self._pa_analysis_complete() else "强烈推荐先完成 PA 技术分析"
        )

    def set_pa_payload(self, payload: Mapping[str, Any]) -> None:
        self._payload = dict(payload)
        self._update_run_tooltip()
        self.set_symbol(str(payload.get("symbol") or ""))
        self._overview_flow.reset()
        self._overview_flow.set_status(0, "done")
        self._overview_stream.clear()
        stock_name = str(payload.get("stock_name") or "").strip()
        symbol = str(payload.get("symbol") or "").strip()
        stage2_ready = isinstance(payload.get("stage2_decision"), Mapping)
        pa_conclusion = (
            "允许交易" if payload.get("should_trade") else "不交易"
        ) if stage2_ready else "暂无已完成的 PA 技术分析结果"
        handoff_summary = {
            "分析对象": stock_name or symbol or "未选择品种",
            "决策基准": self._decision_point_text(payload.get("decision_point")),
            "PA 技术结论": pa_conclusion,
        }
        self._overview.set_grouped_payload(
            [
                [
                    ("分析对象", handoff_summary["分析对象"], 1),
                    ("决策基准", handoff_summary["决策基准"], 1),
                ],
                [("PA 技术结论", handoff_summary["PA 技术结论"], 1)],
            ],
            handoff_summary,
        )
        waiting = {
            "推演状态": "等待运行二阶推演",
            "分析对象": stock_name or symbol or "未选择品种",
            "待生成内容": "主导参与者、主导参与者行为推演及关键证据",
        }
        self._reasoning.set_payload(waiting, waiting)
        configured_sector = self._symbol_preference(symbol, "sector_name") or str(
            payload.get("sector_name") or ""
        ).strip()
        if hasattr(self, "_sector_name_edit"):
            self._sector_name_edit.setText(configured_sector)
        configured_sector_code = self._symbol_preference(symbol, "sector_code") or str(
            payload.get("sector_code") or ""
        ).strip()
        if hasattr(self, "_sector_code_edit"):
            self._sector_code_edit.setText(configured_sector_code)
        if self._result_matches_payload(payload):
            # Re-entering the 二阶博弈 tab re-sends PA's handoff payload. Keep the
            # just-finished result visible instead of replacing it with placeholders.
            self._render_analysis_result(
                self._last_result_envelope or {}, refresh_history=False
            )
            return

    def _result_matches_payload(self, payload: Mapping[str, Any]) -> bool:
        envelope = self._last_result_envelope
        if not isinstance(envelope, Mapping):
            return False
        result = envelope.get("result")
        result = result if isinstance(result, Mapping) else {}
        input_ = result.get("input")
        input_ = input_ if isinstance(input_, Mapping) else {}
        if self._symbol_key(str(input_.get("symbol") or "")) != self._symbol_key(
            str(payload.get("symbol") or "")
        ):
            return False
        materials = input_.get("materials")
        materials = materials if isinstance(materials, Mapping) else {}
        sector = materials.get("sector_analysis")
        sector = sector if isinstance(sector, Mapping) else {}
        result_code = str(sector.get("sector_code") or "").strip()
        payload_code = str(payload.get("sector_code") or "").strip()
        if result_code and payload_code and result_code != payload_code:
            return False
        result_name = str(sector.get("sector_name") or "").strip()
        payload_name = str(payload.get("sector_name") or "").strip()
        return not (result_name and payload_name and result_name != payload_name)

    @staticmethod
    def _symbol_key(symbol: str) -> str:
        value = str(symbol or "").strip().upper().replace(" ", "")
        if value.isdigit() and len(value) == 6:
            value = ("SH." if value.startswith(("5", "6", "9")) else "SZ.") + value
        elif value.endswith(".SH") or value.endswith(".SZ"):
            code, exchange = value.rsplit(".", 1)
            value = exchange + "." + code
        return value

    def _symbol_preference(self, symbol: str, field: str) -> str | None:
        settings = getattr(self._pa_settings, "second_order", None)
        preferences = getattr(settings, "symbol_preferences", {}) or {}
        item = preferences.get(self._symbol_key(symbol), {})
        value = item.get(field) if isinstance(item, Mapping) else None
        return str(value).strip() if isinstance(value, str) else None

    def _load_symbol_preferences(self, symbol: str) -> None:
        if not hasattr(self, "_sector_name_edit"):
            return
        self._sector_name_edit.setText(
            self._symbol_preference(symbol, "sector_name") or ""
        )
        self._sector_code_edit.setText(
            self._symbol_preference(symbol, "sector_code") or ""
        )

    def _persist_symbol_preferences(self, second_order_settings: Any | None = None) -> None:
        symbol = self._symbol_key(self._symbol.text())
        if not symbol or self._pa_settings is None:
            return
        settings = second_order_settings or self._pa_settings.second_order
        preferences = dict(getattr(settings, "symbol_preferences", {}) or {})
        preferences[symbol] = {
            "sector_name": self._sector_name_edit.text().strip(),
            "sector_code": self._sector_code_edit.text().strip(),
        }
        settings.symbol_preferences = preferences

    def _save_trade_rules(self, text: str) -> bool:
        """Persist global rules separately from the current instrument settings."""
        if self._pa_settings is None:
            QMessageBox.warning(self, "无法保存", "PA 设置未初始化")
            return False
        try:
            from pa_agent.config.paths import SETTINGS_JSON_PATH
            from pa_agent.config.settings import load_settings, save_settings

            latest = load_settings(SETTINGS_JSON_PATH)
            latest.second_order.trade_rules = text
            save_settings(latest, SETTINGS_JSON_PATH)
            persisted = load_settings(SETTINGS_JSON_PATH).second_order
            if persisted.trade_rules != text:
                raise OSError("settings.json write verification failed")
            self._pa_settings.second_order = persisted.model_copy(deep=True)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc) or type(exc).__name__)
            return False
        self._status.setText("交易规则已保存")
        return True

    @staticmethod
    def _decision_point_text(value: object) -> str:
        return {
            "close": "收盘决策点（15:00）",
            "midday": "午盘决策点（11:30）",
            "收盘": "收盘决策点（15:00）",
            "午盘": "午盘决策点（11:30）",
        }.get(str(value or "").strip(), "等待确认决策点")

    def run_automatic_analysis(self) -> None:
        """Run stage three after PA completes stage two for an explicit T+1 symbol."""
        self._run_analysis()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 0, 12, 0)
        root.setSpacing(6)
        self._external_controls = QWidget()
        controls = QHBoxLayout(self._external_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addStretch(1)
        self._symbol = QLineEdit("600519.SH", self._external_controls)
        self._symbol.hide()
        controls.addWidget(QLabel("周期 120 分钟"))
        self._fetch = QPushButton("获取数据")
        self._fetch.clicked.connect(self._fetch_kline)
        controls.addWidget(self._fetch)
        self._run = QPushButton("运行二阶推演")
        self._run.setObjectName("accentButton")
        self._run.setToolTip("强烈推荐先完成 PA 技术分析")
        self._run.clicked.connect(self._run_analysis)
        controls.addWidget(self._run)
        self._status = QLabel("", self._external_controls)
        self._status.setObjectName("mutedLabel")
        self._status.hide()

        self._prior_disclaimer = QLabel("专家先验推演，非统计估计")
        self._prior_disclaimer.setObjectName("warningLabel")
        root.addWidget(self._prior_disclaimer)

        split = QSplitter(Qt.Orientation.Horizontal, self)
        self._content_split = split
        split.setChildrenCollapsible(False)
        chart_host = QWidget(split)
        chart_layout = QVBoxLayout(chart_host)
        chart_layout.setContentsMargins(0, 0, 0, 60)
        chart_layout.setSpacing(36)
        self._chart_legend = create_second_order_chart_legend(chart_host)
        chart_layout.addWidget(self._chart_legend)
        self._chart = SecondOrderGameChart(chart_host)
        chart_layout.addWidget(self._chart)
        split.addWidget(chart_host)
        self._tabs = QTabWidget(split)
        self._overview = self._text_tab("等待 PA 阶段 2 数据或手动获取 K 线。")
        # 概览只保留原始数据入口；分析卡片在对应业务标签页中展示。
        self._overview._scroll.hide()
        self._overview.setMaximumHeight(48)
        self._overview_flow = _AnalysisFlowCard()
        self._overview_stream = _StreamingProgressView()
        overview_tab = QWidget()
        self._overview_tab = overview_tab
        overview_layout = QVBoxLayout(overview_tab)
        overview_layout.setContentsMargins(8, 8, 8, 8)
        overview_layout.setSpacing(10)
        self._labeler_status_card = _LabelerStatusCard()
        overview_layout.addWidget(self._labeler_status_card)
        overview_layout.addWidget(self._overview_flow)
        overview_layout.addWidget(self._overview_stream)
        overview_layout.addWidget(self._overview, 1)
        self._cycle = self._prototype_tab("cycle", "等待情绪周期模型根据板块材料给出五档观测。")
        self._game_reasoning = self._prototype_tab("game", "等待参与者、行为与概率推演。")
        self._reasoning = self._game_reasoning
        self._tree = self._prototype_tab("tree", "超预期强 / 符合预期 / 低于预期三分支将在结果返回后显示。")
        self._tree.set_trade_rules(
            str(
                getattr(
                    getattr(self._pa_settings, "second_order", None),
                    "trade_rules",
                    "",
                )
                or ""
            ),
            self._save_trade_rules,
        )
        self._gate = self._text_tab("T+1 独立闸门：等待 PA 原闸门与二阶数据。")
        self._gate.set_header_subtitle("什么时候该有信心，什么时候不该")
        self._sector = self._prototype_tab("sector", "请在设置页填写与个股高度相关的板块名称。")
        market_tab = QWidget()
        market_layout = QVBoxLayout(market_tab)
        market_layout.setContentsMargins(8, 8, 8, 8)
        market_layout.setSpacing(6)
        self._market_regenerate = QPushButton("重新生成")
        self._market_regenerate.setToolTip("立即强制重新生成大盘分析")
        self._market_regenerate.clicked.connect(self._regenerate_market_analysis)
        self._market = self._prototype_tab("market", "等待读取 DSA 大盘分析缓存。")
        self._market.set_title("大盘分析")
        self._market.set_header_note(
            "半天内程序仅自动生成一次；需要即时更新时可手动重新生成。"
        )
        self._market.add_header_action(self._market_regenerate)
        market_layout.addWidget(self._market, 1)

        # Keep the primary navigation short and group the views that answer
        # the same user question.  This mirrors the prototype: the outer tab
        # names the work area, while the inner tab switches its perspective.
        self._game_tabs = self._make_subtabs(
            "gameAnalysisSubTabs",
            (
                ("博弈推演", self._game_reasoning),
                ("板块分析", self._sector),
                ("大盘分析", market_tab),
            ),
        )
        self._game_tab = self._wrap_subtabs(self._game_tabs)

        self._response_tabs = self._make_subtabs(
            "responseAnalysisSubTabs",
            (
                ("应对树", self._tree),
                ("T+1入场信心", self._gate),
            ),
        )
        self._response_tab = self._wrap_subtabs(self._response_tabs)

        # Set titles explicitly because these panels now live below a
        # compound tab and are no longer named by the outer tab loop.
        self._game_reasoning.set_title("博弈推演")
        self._sector.set_title("板块分析")
        self._tree.set_title("应对树")
        self._gate.set_title("T+1入场信心")

        self._materials = self._build_material_runtime_tab()
        self._history = self._build_history_tab()
        self._chat = self._build_chat_tab()
        self._raw_tab = self._build_raw_tab()
        self._settings = self._build_settings_tab()
        for name, widget in (
            ("概览", overview_tab),
            ("情绪周期", self._cycle),
            ("博弈推演", self._game_tab),
            ("应对方案", self._response_tab),
            ("材料缓存", self._materials),
            ("历史回测", self._history),
            ("LLM 对话", self._chat),
            ("原始", self._raw_tab),
            ("设置", self._settings),
        ):
            self._tabs.addTab(widget, name)
            if isinstance(widget, _AnalysisResultPanel):
                widget.set_title(name)
        self._install_more_menu()
        self._tabs.currentChanged.connect(self._on_analysis_tab_changed)
        self._tabs.setMinimumWidth(430)
        self._tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # The nested tab strip sits inside this outer pane.  Qt's native base
        # painter otherwise draws a bright horizontal guide through the top of
        # the nested buttons; the prototype only uses the explicit dark
        # divider on the nested bar's bottom edge.
        self._tabs.tabBar().setDrawBase(False)
        self._tabs.setStyleSheet(
            "QTabWidget::pane { background: #0C0E11; border: none; "
            "border-top: 0px; top: 0px; margin: 0px; } "
            "QTabBar::tab:focus { outline: none; } "
            "QTabBar::tab:selected { border-bottom: 1px solid #333A45; }"
        )
        split.setStretchFactor(0, 40)
        split.setStretchFactor(1, 60)
        split.setSizes([448, 672])
        root.addWidget(split, 1)

    @staticmethod
    def _make_subtabs(
        object_name: str,
        tabs: tuple[tuple[str, QWidget], ...],
    ) -> QTabWidget:
        """Create a compact, keyboard-accessible tab bar for a compound view."""
        widget = QTabWidget()
        widget.setObjectName(object_name)
        widget.setDocumentMode(True)
        widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Qt's native tab-base painter draws an extra bright guide line
        # behind the buttons.  The prototype only has the explicit divider
        # below the buttons, so disable the native base before applying QSS.
        widget.tabBar().setDrawBase(False)
        style = (
            # Figma selection 4:269 / 5:625 uses the same deep-gray stroke
            # (#2B3542) around the sub-tabs.  The divider belongs to the
            # tab-bar's bottom edge; the pane itself must not add a second
            # (often white, theme-provided) line on its top edge.
            f"QTabWidget#{object_name}::pane {{ background: #111820; "
            "border: none; top: 0px; margin: 0px; } "
            "QTabWidget::pane { background: #111820; border: none; "
            "top: 0px; margin: 0px; } "
            f"QTabWidget#{object_name} QStackedWidget#qt_tabwidget_stackedwidget {{ "
            "background: #111820; border: none; } "
            "QTabBar { border-bottom: 1px solid #2B3542; } "
            "QTabBar::tab { padding: 5px 14px; margin-right: 2px; "
            "color: #9AAAC0; background: #111820; "
            "border: 1px solid #2B3542; border-bottom: none; } "
            "QTabBar::tab:hover { color: #EFF4FB; background: #111820; } "
            "QTabBar::tab:selected { color: #EFF4FB; background: #111820; "
            "border-color: #2B3542; border-bottom: none; } "
            "QTabBar::tab:focus { outline: none; }"
        )
        for label, page in tabs:
            widget.addTab(page, label)
        # Reapply after tab insertion so the style reaches Qt's lazily-created
        # pane/stacked-widget children as well as the tab bar itself.
        widget.setStyleSheet(style)
        return widget

    @staticmethod
    def _wrap_subtabs(subtabs: QTabWidget) -> QWidget:
        """Give nested tabs the same breathing room as the other side-panel views."""
        container = QWidget()
        layout = QVBoxLayout(container)
        # Keep the primary/sub-tab rhythm, but reduce it by the requested 5px
        # from the previous 20px value.
        layout.setContentsMargins(0, 15, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(subtabs)

        # The content page currently carries its own 10px top inset.  Since
        # the divider now sits directly on the tab-bar bottom edge, remove
        # that extra inset inside compound tabs only (without changing the
        # spacing of standalone result panels).
        for index in range(subtabs.count()):
            page = subtabs.widget(index)
            page_layout = page.layout()
            if page_layout is None:
                continue
            margins = page_layout.contentsMargins()
            page_layout.setContentsMargins(
                margins.left(),
                max(0, margins.top() - 10),
                margins.right(),
                margins.bottom(),
            )
        return container

    def control_bar(self) -> QWidget:
        """Return the host-owned second-order control bar."""
        return self._external_controls

    @staticmethod
    def _text_tab(text: str) -> _AnalysisResultPanel:
        return _AnalysisResultPanel(text)

    @staticmethod
    def _prototype_tab(page: str, text: str) -> _AnalysisResultPanel:
        from pa_agent.gui.second_order_cards import PrototypeAnalysisPanel

        return PrototypeAnalysisPanel(page, text)

    def _build_chat_tab(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)
        conversation = QWidget()
        conversation_layout = QVBoxLayout(conversation)
        conversation_layout.setContentsMargins(0, 0, 0, 0)
        conversation_layout.setSpacing(8)
        self._chat_transcript = QPlainTextEdit(
            "二阶博弈独立对话。仅复用 PA 的模型连接配置，不读取 PA 技术分析或自由对话上下文。"
        )
        self._chat_transcript.setReadOnly(True)
        conversation_layout.addWidget(self._chat_transcript, 1)

        prompt_row = QHBoxLayout()
        prompt_row.setContentsMargins(0, 0, 0, 0)
        self._chat_prompt_toggle = QToolButton()
        self._chat_prompt_toggle.setText("提示词 ▸")
        self._chat_prompt_toggle.setCheckable(True)
        self._chat_prompt_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._chat_prompt_toggle.toggled.connect(self._toggle_chat_prompt_library)
        prompt_row.addWidget(self._chat_prompt_toggle)
        self._chat_quick_prompt_buttons: list[QPushButton] = []
        for _ in range(3):
            button = QPushButton()
            button.setObjectName("quickPromptButton")
            button.setMaximumWidth(168)
            button.setVisible(False)
            button.clicked.connect(
                lambda _checked=False, target=button: self._insert_chat_quick_prompt(target)
            )
            prompt_row.addWidget(button)
            self._chat_quick_prompt_buttons.append(button)
        prompt_row.addStretch(1)
        conversation_layout.addLayout(prompt_row)

        self._chat_prompt_list = QListWidget()
        self._chat_prompt_list.setMaximumHeight(140)
        self._chat_prompt_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._chat_prompt_list.itemClicked.connect(self._insert_chat_prompt)
        self._chat_prompt_list.customContextMenuRequested.connect(
            self._show_chat_prompt_context_menu
        )
        self._chat_prompt_list_panel = QWidget()
        prompt_list_layout = QVBoxLayout(self._chat_prompt_list_panel)
        prompt_list_layout.setContentsMargins(0, 0, 0, 0)
        prompt_list_actions = QHBoxLayout()
        prompt_list_actions.setContentsMargins(0, 0, 0, 4)
        self._add_chat_prompt_button = QPushButton("+ 添加提示词")
        self._add_chat_prompt_button.setObjectName("ghostButton")
        self._add_chat_prompt_button.clicked.connect(self._add_chat_prompt)
        prompt_list_actions.addWidget(self._add_chat_prompt_button)
        prompt_list_actions.addStretch(1)
        prompt_list_layout.addLayout(prompt_list_actions)
        prompt_list_layout.addWidget(self._chat_prompt_list)
        self._chat_prompt_list_panel.setVisible(False)
        conversation_layout.addWidget(self._chat_prompt_list_panel)

        row = QHBoxLayout()
        self._chat_input = QLineEdit()
        self._chat_input.setPlaceholderText("输入补充材料或追问")
        self._chat_input.returnPressed.connect(self._send_chat_context)
        row.addWidget(self._chat_input, 1)
        send = QPushButton("发送")
        send.clicked.connect(self._send_chat_context)
        row.addWidget(send)
        clear = QPushButton("清空")
        clear.clicked.connect(self._clear_chat_context)
        row.addWidget(clear)
        conversation_layout.addLayout(row)
        layout.addWidget(conversation, 3)

        audit_panel = QFrame()
        audit_panel.setObjectName("secondOrderPromptAudit")
        audit_layout = QVBoxLayout(audit_panel)
        audit_layout.setContentsMargins(10, 10, 10, 10)
        audit_layout.setSpacing(8)
        audit_title = QLabel("本次注入的提示词")
        audit_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        audit_layout.addWidget(audit_title)
        self._prompt_files_list = QListWidget()
        self._prompt_files_list.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        audit_layout.addWidget(self._prompt_files_list, 1)
        self._sent_source_button = QPushButton("展开发送原文")
        self._sent_source_button.setCheckable(True)
        self._sent_source_button.toggled.connect(self._toggle_sent_source)
        audit_layout.addWidget(self._sent_source_button)
        self._sent_source_text = QPlainTextEdit()
        self._sent_source_text.setReadOnly(True)
        self._sent_source_text.setPlaceholderText("完成二阶推演后显示实际发送给模型的全部原文")
        self._sent_source_text.setVisible(False)
        audit_layout.addWidget(self._sent_source_text, 2)
        layout.addWidget(audit_panel, 2)
        self._refresh_chat_prompt_list()
        self._update_llm_trace([])
        return widget

    def _toggle_chat_prompt_library(self, visible: bool) -> None:
        self._chat_prompt_toggle.setText("提示词 ▾" if visible else "提示词 ▸")
        self._chat_prompt_list_panel.setVisible(visible)
        if visible:
            self._refresh_chat_prompt_list()

    def _refresh_chat_prompt_list(self) -> None:
        self._chat_prompt_library = type(self._chat_prompt_library)()
        self._chat_prompt_list.clear()
        snippets = list(self._chat_prompt_library.items)
        snippets.sort(
            key=lambda snippet: -self._chat_prompt_usage.get(snippet.id, 0)
        )
        for button, snippet in zip(
            self._chat_quick_prompt_buttons, snippets[:3], strict=False
        ):
            button.setText(snippet.name)
            button.setToolTip(snippet.text)
            button.setProperty("promptId", snippet.id)
            button.setVisible(True)
        for button in self._chat_quick_prompt_buttons[len(snippets[:3]) :]:
            button.setVisible(False)
            button.setProperty("promptId", None)
        for snippet in snippets:
            item = QListWidgetItem(snippet.name)
            item.setData(Qt.ItemDataRole.UserRole, snippet.id)
            item.setToolTip(snippet.text)
            self._chat_prompt_list.addItem(item)

    def _insert_chat_prompt_id(self, item_id: object) -> None:
        snippet = (
            self._chat_prompt_library.get(item_id)
            if isinstance(item_id, str)
            else None
        )
        if snippet is None:
            return
        cursor = self._chat_input.cursorPosition()
        current = self._chat_input.text()
        self._chat_input.setText(current[:cursor] + snippet.text + current[cursor:])
        self._chat_input.setCursorPosition(cursor + len(snippet.text))
        self._chat_input.setFocus()
        self._chat_prompt_usage[snippet.id] = (
            self._chat_prompt_usage.get(snippet.id, 0) + 1
        )
        self._refresh_chat_prompt_list()

    def _insert_chat_quick_prompt(self, button: QPushButton) -> None:
        self._insert_chat_prompt_id(button.property("promptId"))

    def _insert_chat_prompt(self, list_item: QListWidgetItem) -> None:
        self._insert_chat_prompt_id(list_item.data(Qt.ItemDataRole.UserRole))

    def _show_chat_prompt_context_menu(self, position) -> None:
        list_item = self._chat_prompt_list.itemAt(position)
        if list_item is None:
            return
        item_id = list_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(item_id, str):
            return
        menu = QMenu(self)
        edit_action = menu.addAction("编辑提示词...")
        edit_action.triggered.connect(lambda: self._edit_chat_prompt(item_id))
        delete_action = menu.addAction("删除提示词")
        delete_action.triggered.connect(lambda: self._delete_chat_prompt(item_id))
        menu.exec(self._chat_prompt_list.viewport().mapToGlobal(position))

    def _add_chat_prompt(self) -> None:
        self._edit_chat_prompt()

    def _edit_chat_prompt(self, item_id: str | None = None) -> None:
        snippet = (
            self._chat_prompt_library.get(item_id) if item_id is not None else None
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑提示词" if snippet is not None else "新增提示词")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        name_edit = QLineEdit(snippet.name if snippet is not None else "")
        text_edit = QPlainTextEdit(snippet.text if snippet is not None else "")
        text_edit.setMinimumHeight(130)
        form.addRow("名称", name_edit)
        form.addRow("内容", text_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if snippet is None:
                self._chat_prompt_library.add(
                    name=name_edit.text(), text=text_edit.toPlainText()
                )
            else:
                self._chat_prompt_library.update(
                    snippet.id, name=name_edit.text(), text=text_edit.toPlainText()
                )
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存提示词", str(exc))
            return
        self._refresh_chat_prompt_list()

    def _delete_chat_prompt(self, item_id: str) -> None:
        snippet = self._chat_prompt_library.get(item_id)
        if snippet is None:
            return
        answer = QMessageBox.question(
            self,
            "删除提示词",
            f"确定删除提示词“{snippet.name}”？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._chat_prompt_library.remove(item_id)
            self._refresh_chat_prompt_list()

    def _toggle_sent_source(self, expanded: bool) -> None:
        self._sent_source_text.setVisible(expanded)
        self._sent_source_button.setText("收起发送原文" if expanded else "展开发送原文")

    def _update_llm_trace(self, trace: object) -> None:
        entries = list(trace) if isinstance(trace, list | tuple) else []
        ordered_files: list[str] = []
        raw_sections: list[str] = []
        for call_index, entry in enumerate(entries, 1):
            if not isinstance(entry, Mapping):
                continue
            for path_value in entry.get("prompt_files") or ():
                name = Path(str(path_value)).name
                if name and name not in ordered_files:
                    ordered_files.append(name)
            request_name = str(entry.get("request") or f"调用 {call_index}")
            for message_index, message in enumerate(entry.get("messages") or (), 1):
                if not isinstance(message, Mapping):
                    continue
                role = str(message.get("role") or "unknown")
                content = str(message.get("content") or "")
                raw_sections.append(
                    f"===== {call_index}. {request_name} / {message_index}. {role} =====\n{content}"
                )
        self._prompt_files_list.clear()
        if ordered_files:
            for index, name in enumerate(ordered_files, 1):
                self._prompt_files_list.addItem(QListWidgetItem(f"{index}. {name}"))
        else:
            item = QListWidgetItem("尚未运行二阶推演")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._prompt_files_list.addItem(item)
        self._sent_source_text.setPlainText("\n\n".join(raw_sections))

    def _build_raw_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        self._raw_text = QPlainTextEdit("尚未记录二阶博弈模型调用。")
        self._raw_text.setReadOnly(True)
        layout.addWidget(self._raw_text, 1)
        return widget

    def _install_more_menu(self) -> None:
        menu = QMenu(self._tabs)
        for label, widget in (("原始", self._raw_tab), ("设置", self._settings)):
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, page=widget: self._tabs.setCurrentWidget(page)
            )
            self._tabs.tabBar().setTabVisible(self._tabs.indexOf(widget), False)
        self._more_button = QToolButton(self._tabs)
        self._more_button.setText("更多")
        self._more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_button.setMenu(menu)
        self._tabs.setCornerWidget(
            self._more_button, Qt.Corner.TopRightCorner
        )

    def _render_llm_trace(self, envelope: Mapping[str, Any]) -> None:
        trace = envelope.get("llm_trace")
        calls = list(trace) if isinstance(trace, list | tuple) else []
        self._update_llm_trace(calls)
        self._raw_text.setPlainText(
            json.dumps(
                {
                    "status": envelope.get("status"),
                    "error": envelope.get("error"),
                    "calls": calls,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )

    def _build_history_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        toolbar = QHBoxLayout()
        self._history_id_spin = QSpinBox()
        self._history_id_spin.setRange(0, 2_147_483_647)
        self._history_id_spin.setPrefix("记录 #")
        toolbar.addWidget(self._history_id_spin)
        resolve_win = QPushButton("标记胜出")
        resolve_win.clicked.connect(lambda: self._resolve_history("win"))
        toolbar.addWidget(resolve_win)
        resolve_loss = QPushButton("标记失效")
        resolve_loss.clicked.connect(lambda: self._resolve_history("loss"))
        toolbar.addWidget(resolve_loss)
        resolve_neutral = QPushButton("标记持平")
        resolve_neutral.clicked.connect(lambda: self._resolve_history("neutral"))
        toolbar.addWidget(resolve_neutral)
        replay = QPushButton("回放推演过程")
        replay.setToolTip("在概览页回放该记录的实时推演流")
        replay.clicked.connect(self._replay_history)
        toolbar.addWidget(replay)
        raw_stream = QPushButton("后端数据流")
        raw_stream.setCheckable(True)
        raw_stream.setToolTip("展开/收起该记录的后端数据流（原始分析过程与消息）")
        raw_stream.toggled.connect(self._toggle_history_raw_stream)
        toolbar.addWidget(raw_stream)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self._history_summary_label = QLabel("已结算 0 / 30，样本不足，暂不发布胜率。")
        layout.addWidget(self._history_summary_label)
        self._history_calibration = _CalibrationSummaryPanel()
        layout.addWidget(self._history_calibration)
        self._history_table = QTableWidget(0, 6)
        self._history_table.setObjectName("secondOrderHistoryTable")
        self._history_table.setStyleSheet(
            "QTableWidget#secondOrderHistoryTable::item:selected {"
            " background: transparent; color: #E8ECF1; }"
        )
        self._history_table.setHorizontalHeaderLabels(
            ["编号", "品种", "板块", "决策点", "完成时间", "标记"]
        )
        self._history_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._history_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._history_table.customContextMenuRequested.connect(
            self._show_history_context_menu
        )
        self._history_table.itemSelectionChanged.connect(self._history_selection_changed)
        self._history_table.cellDoubleClicked.connect(
            lambda _row, _column: self._open_selected_history()
        )
        layout.addWidget(self._history_table, 2)
        self._history_text = QPlainTextEdit(
            "选择记录即可预览历史分析，双击打开完整结果。"
        )
        self._history_text.setReadOnly(True)
        self._history_text.setVisible(False)
        layout.addWidget(self._history_text, 2)
        return widget

    def _on_analysis_tab_changed(self, _index: int) -> None:
        current = self._tabs.currentWidget()
        if current is self._history:
            self._refresh_history()
        elif current is self._materials:
            self._refresh_material_runtime()
        self._refresh_labeler_status()
        if current is self._overview_tab:
            self._prime_labeler_catchup()

    def _prime_labeler_catchup(self) -> None:
        """Start one labeler catch-up when the overview is first shown.

        The catch-up is only primed from a user-visible overview visit (never
        from the polling timer), guarded by ``mark_running`` on the backend so
        repeated primes are harmless.  This keeps automated UI tests free of
        background workers while still making the card live on first look.
        """
        if self._labeler_catchup_requested or not self._last_symbol or self._workers:
            return
        try:
            status = _shared_labeler_status_tracker().snapshot()
        except Exception:  # noqa: BLE001 — SecondOrderGame not importable yet
            return
        if status.load_state.value != "not_loaded" or status.run_state.value != "idle":
            self._labeler_catchup_requested = True
            return
        self._start_worker("labeler_catchup", {"symbol": self._last_symbol})
        self._labeler_catchup_requested = True

    def _refresh_labeler_status(self) -> None:
        """Poll the shared tracker into the overview card (render only)."""
        try:
            tracker = _shared_labeler_status_tracker()
        except Exception:  # noqa: BLE001 — SecondOrderGame not importable yet
            return
        try:
            status = tracker.snapshot()
        except Exception:  # noqa: BLE001
            return
        self._labeler_status_card.set_status(status)

    def _build_material_runtime_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        settings = getattr(self._pa_settings, "second_order", None)
        run_controls = QHBoxLayout()
        self._run_news_prefetch_toggle = QCheckBox("正式推演前强制预取消息")
        self._run_news_prefetch_toggle.setChecked(True)
        self._run_news_prefetch_toggle.setEnabled(False)
        self._run_news_prefetch_toggle.setToolTip(
            "必须先完成消息预取，才允许进入情绪/材料预分析。"
        )
        run_controls.addWidget(self._run_news_prefetch_toggle)
        self._run_material_preanalysis_toggle = QCheckBox("正式推演前强制预分析材料")
        self._run_material_preanalysis_toggle.setChecked(True)
        self._run_material_preanalysis_toggle.setEnabled(False)
        self._run_material_preanalysis_toggle.setToolTip(
            "必须先取得并冻结情绪材料，才允许进入正式二阶推演。"
        )
        run_controls.addWidget(self._run_material_preanalysis_toggle)
        run_controls.addStretch()
        layout.addLayout(run_controls)
        controls = QHBoxLayout()
        self._news_prefetch_toggle = QCheckBox("定时消息预取")
        self._news_prefetch_toggle.toggled.connect(self._set_news_prefetch_enabled)
        controls.addWidget(self._news_prefetch_toggle)
        self._news_prefetch_time = QTimeEdit()
        self._news_prefetch_time.setDisplayFormat("HH:mm")
        self._news_prefetch_time.timeChanged.connect(self._update_material_timers)
        self._news_prefetch_time.timeChanged.connect(
            self._on_material_runtime_settings_changed
        )
        controls.addWidget(self._news_prefetch_time)
        run_news = QPushButton("立即预取")
        run_news.clicked.connect(self._run_news_prefetch)
        controls.addWidget(run_news)
        controls.addWidget(QLabel("预取条数"))
        self._news_prefetch_count_spin = QSpinBox()
        self._news_prefetch_count_spin.setRange(5, 30)
        self._news_prefetch_count_spin.setValue(
            int(getattr(settings, "max_news_items", 18) or 18)
        )
        self._news_prefetch_count_spin.setToolTip(
            "每次消息预取每板块拉取的条数（5~30）"
        )
        self._news_prefetch_count_spin.valueChanged.connect(
            self._on_material_runtime_settings_changed
        )
        controls.addWidget(self._news_prefetch_count_spin)
        self._material_preanalysis_toggle = QCheckBox("定时材料预分析")
        self._material_preanalysis_toggle.toggled.connect(
            self._set_material_preanalysis_enabled
        )
        controls.addWidget(self._material_preanalysis_toggle)
        self._material_preanalysis_time = QTimeEdit()
        self._material_preanalysis_time.setDisplayFormat("HH:mm")
        self._material_preanalysis_time.timeChanged.connect(self._update_material_timers)
        self._material_preanalysis_time.timeChanged.connect(
            self._on_material_runtime_settings_changed
        )
        controls.addWidget(self._material_preanalysis_time)
        run_materials = QPushButton("立即预分析")
        run_materials.clicked.connect(self._run_material_preanalysis)
        controls.addWidget(run_materials)
        raw_fields = QPushButton("原始字段")
        raw_fields.setCheckable(True)
        raw_fields.setToolTip("展开/收起材料缓存原始字段（任务状态、缓存生命周期等）")
        raw_fields.toggled.connect(self._toggle_raw_material_fields)
        controls.addWidget(raw_fields)
        controls.addStretch()
        layout.addLayout(controls)
        import_controls = QHBoxLayout()
        import_controls.addWidget(QLabel("用户导入："))
        self._import_experience_btn = QPushButton("导入经验")
        self._import_experience_btn.setToolTip(
            "编辑通用提示词目录下的用户经验 TXT，每轮分析注入大模型"
        )
        self._import_experience_btn.clicked.connect(self._open_user_experience_editor)
        import_controls.addWidget(self._import_experience_btn)
        self._import_news_btn = QPushButton("导入消息")
        self._import_news_btn.setToolTip(
            "导入一条自认为有价值的新闻到预分析列表置顶"
        )
        self._import_news_btn.clicked.connect(self._open_import_news_dialog)
        import_controls.addWidget(self._import_news_btn)
        import_controls.addStretch()
        layout.addLayout(import_controls)
        news_header = QLabel("今日缓存新闻详情")
        news_header.setStyleSheet("font-size: 13px; font-weight: 600; color: #9AA5B1;")
        layout.addWidget(news_header)
        self._material_news_table = QTreeWidget()
        self._material_news_table.setObjectName("secondOrderMaterialNewsTable")
        self._material_news_table.setStyleSheet(
            "QTreeWidget#secondOrderMaterialNewsTable::item:selected {"
            " background: transparent; color: #E8ECF1; }"
        )
        self._material_news_table.setColumnCount(11)
        self._material_news_table.setHeaderLabels(
            [
                "板块",
                "标题",
                "URL",
                "日期",
                "代码",
                "来源",
                "最终加权分",
                "相关性",
                "有效期",
                "可信度",
                "主体目的",
            ]
        )
        self._material_news_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._material_news_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._material_news_table.setWordWrap(False)
        self._material_news_table.setAlternatingRowColors(True)
        self._material_news_table.setRootIsDecorated(True)
        self._material_news_table.header().setStretchLastSection(True)
        self._material_news_table.itemClicked.connect(self._on_material_news_item_clicked)
        layout.addWidget(self._material_news_table, 2)
        self._material_runtime_text = QPlainTextEdit(
            "消息预取与材料预分析均未开启。可在此查看任务状态、失败原因和缓存条目。"
        )
        self._material_runtime_text.setReadOnly(True)
        self._material_runtime_text.setVisible(False)
        layout.addWidget(self._material_runtime_text, 1)

        self._news_prefetch_time.setTime(
            QTime.fromString(str(getattr(settings, "news_prefetch_schedule", "09:35")), "HH:mm")
        )
        self._material_preanalysis_time.setTime(
            QTime.fromString(
                str(getattr(settings, "material_preanalysis_schedule", "09:40")),
                "HH:mm",
            )
        )
        self._news_prefetch_timer = QTimer(self)
        self._material_preanalysis_timer = QTimer(self)
        self._news_prefetch_timer.setSingleShot(True)
        self._material_preanalysis_timer.setSingleShot(True)
        self._news_prefetch_toggle.setChecked(
            bool(getattr(settings, "news_prefetch_enabled", False))
        )
        self._material_preanalysis_toggle.setChecked(
            bool(getattr(settings, "material_preanalysis_enabled", False))
        )
        self._update_material_timers()
        return widget

    def _build_settings_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()
        provider = getattr(self._pa_settings, "provider", None)
        self._llm_url_value = QLabel()
        self._llm_model_value = QLabel()
        self._thinking_value = QLabel()
        self._reasoning_value = QLabel()
        form.addRow("LLM API URL", self._llm_url_value)
        form.addRow("模型名称", self._llm_model_value)
        form.addRow("思考模式", self._thinking_value)
        form.addRow("推理强度", self._reasoning_value)
        layout.addLayout(form)
        futu = getattr(self._pa_settings, "futu", None)
        self._market_source_combo = QComboBox()
        self._market_source_combo.addItem("Futu OpenD（默认）", "futu")
        self._market_source_combo.addItem("AkShare（无需 Key）", "akshare")
        configured_source = str(
            getattr(
                getattr(self._pa_settings, "second_order", None),
                "market_data_source",
                "futu",
            )
            or "futu"
        )
        source_index = self._market_source_combo.findData(configured_source)
        self._market_source_combo.setCurrentIndex(max(source_index, 0))
        form.addRow("120 分钟行情源", self._market_source_combo)
        self._sector_name_edit = QLineEdit()
        self._sector_name_edit.setPlaceholderText("例如：半导体；用于板块新闻与情绪分析")
        form.addRow("关联板块名称", self._sector_name_edit)
        self._sector_code_edit = QLineEdit()
        self._sector_code_edit.setPlaceholderText(
            "例如：SH.LIST0022、HK.LIST1910 或 US.LIST20077"
        )
        self._sector_code_edit.setToolTip(
            "必填。板块代码将原样提交给富途 OpenD，不限制市场或代码格式；"
            "富途无法返回板块数据时会显示具体错误。"
        )
        form.addRow("关联板块代码（必填）", self._sector_code_edit)
        sector_code_note = QLabel(
            "材料预分析会用该代码向富途查询板块；是否有效以富途 OpenD 返回为准。"
        )
        sector_code_note.setWordWrap(True)
        sector_code_note.setStyleSheet("color: #9AA5B1;")
        form.addRow("", sector_code_note)
        second_order_settings = getattr(self._pa_settings, "second_order", None)
        self._dsa_database_edit = QLineEdit(
            str(getattr(second_order_settings, "dsa_database_path", "") or "")
        )
        self._dsa_database_edit.setPlaceholderText(
            r"请选择 DSA 路径中的 data 文件夹，例如 E:\Daily stock analysis\data"
        )
        form.addRow("DSA data 文件夹", self._dsa_database_edit)
        self._futu_host_edit = QLineEdit(
            str(getattr(futu, "opend_host", "127.0.0.1") or "127.0.0.1")
        )
        self._futu_port_spin = QSpinBox()
        self._futu_port_spin.setRange(1, 65535)
        self._futu_port_spin.setValue(int(getattr(futu, "opend_port", 11111) or 11111))
        form.addRow("Futu OpenD 主机", self._futu_host_edit)
        form.addRow("Futu OpenD 端口", self._futu_port_spin)
        self._tavily_key_edit = QLineEdit()
        self._tavily_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._tavily_key_edit.setText(
            str(
                getattr(
                    getattr(self._pa_settings, "second_order", None),
                    "tavily_api_key",
                    "",
                )
                or ""
            )
        )
        self._tavily_key_edit.setPlaceholderText("可选；用于补充新闻材料")
        form.addRow("Tavily API Key", self._tavily_key_edit)
        note = QLabel(
            "二阶博弈为每次推演创建独立模型客户端，只复用 PA 的 URL、模型和凭证；"
            "不会读取 PA 技术分析或自由对话上下文。AkShare 无需 API Key。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        model_settings = QPushButton("打开 PA 模型设置")
        model_settings.clicked.connect(self._open_pa_model_settings)
        actions.addWidget(model_settings)
        save_data = QPushButton("保存数据设置")
        save_data.clicked.connect(self._save_data_settings)
        actions.addWidget(save_data)
        open_config = QPushButton("打开二阶配置")
        open_config.clicked.connect(lambda: self._open_resource("config"))
        actions.addWidget(open_config)
        open_prompts = QPushButton("打开提示词文件夹")
        open_prompts.clicked.connect(lambda: self._open_resource("prompts"))
        actions.addWidget(open_prompts)
        open_history = QPushButton("打开历史记录文件夹")
        open_history.clicked.connect(lambda: self._open_resource("history"))
        actions.addWidget(open_history)
        open_hmm = QPushButton("打开 HMM 参数工作台")
        open_hmm.clicked.connect(lambda: self._open_resource("hmm"))
        actions.addWidget(open_hmm)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        self.refresh_settings()
        return widget

    def _open_pa_model_settings(self) -> None:
        if self._pa_settings is None:
            QMessageBox.warning(self, "无法打开", "PA 设置未初始化")
            return
        self.model_settings_requested.emit()

    def refresh_settings(self) -> None:
        provider = getattr(self._pa_settings, "provider", None)
        self._llm_url_value.setText(
            str(getattr(provider, "base_url", "未配置") or "未配置")
        )
        self._llm_model_value.setText(
            str(getattr(provider, "model", "未配置") or "未配置")
        )
        self._thinking_value.setText(
            "开启" if getattr(provider, "thinking", False) else "关闭"
        )
        self._reasoning_value.setText(
            str(getattr(provider, "reasoning_effort", "medium"))
        )

    def _save_data_settings(self) -> None:
        if self._pa_settings is None:
            QMessageBox.warning(self, "无法保存", "PA 设置未初始化")
            return
        try:
            from pa_agent.config.paths import SETTINGS_JSON_PATH
            from pa_agent.config.settings import load_settings, save_settings

            latest = load_settings(SETTINGS_JSON_PATH)
            latest.futu.opend_host = (
                self._futu_host_edit.text().strip() or "127.0.0.1"
            )
            latest.futu.opend_port = self._futu_port_spin.value()
            latest.second_order.market_data_source = (
                self._market_source_combo.currentData()
            )
            latest.second_order.tavily_api_key = (
                self._tavily_key_edit.text().strip()
            )
            self._persist_symbol_preferences(latest.second_order)
            latest.second_order.dsa_database_path = (
                self._dsa_database_edit.text().strip()
            )
            self._persist_material_runtime_settings(latest.second_order)
            save_settings(latest, SETTINGS_JSON_PATH)

            reloaded = load_settings(SETTINGS_JSON_PATH)
            persisted = reloaded.second_order
            expected = (
                latest.second_order.market_data_source,
                latest.second_order.tavily_api_key,
                latest.second_order.dsa_database_path,
                latest.second_order.run_news_prefetch_enabled,
                latest.second_order.run_material_preanalysis_enabled,
                latest.second_order.news_prefetch_schedule,
                latest.second_order.material_preanalysis_schedule,
                latest.second_order.symbol_preferences,
            )
            actual = (
                persisted.market_data_source,
                persisted.tavily_api_key,
                persisted.dsa_database_path,
                persisted.run_news_prefetch_enabled,
                persisted.run_material_preanalysis_enabled,
                persisted.news_prefetch_schedule,
                persisted.material_preanalysis_schedule,
                persisted.symbol_preferences,
            )
            if actual != expected:
                raise OSError("settings.json write verification failed")
            self._pa_settings.futu = reloaded.futu.model_copy(deep=True)
            self._pa_settings.second_order = persisted.model_copy(deep=True)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc) or type(exc).__name__)
            return
        self._status.setText("二阶数据设置已保存")

    def _persist_material_runtime_settings(self, second_order_settings: Any | None = None) -> None:
        if self._pa_settings is None:
            return
        settings = second_order_settings or self._pa_settings.second_order
        settings.run_news_prefetch_enabled = True
        settings.run_material_preanalysis_enabled = True
        settings.news_prefetch_enabled = self._news_prefetch_toggle.isChecked()
        settings.news_prefetch_schedule = self._news_prefetch_time.time().toString("HH:mm")
        settings.max_news_items = self._news_prefetch_count_spin.value()
        settings.material_preanalysis_enabled = (
            self._material_preanalysis_toggle.isChecked()
        )
        settings.material_preanalysis_schedule = (
            self._material_preanalysis_time.time().toString("HH:mm")
        )

    def _on_material_runtime_settings_changed(self, *_args: object) -> None:
        self._persist_material_runtime_settings()
        if self._building_ui or self._pa_settings is None:
            return
        try:
            from pa_agent.config.paths import SETTINGS_JSON_PATH
            from pa_agent.config.settings import load_settings, save_settings

            latest = load_settings(SETTINGS_JSON_PATH)
            self._persist_material_runtime_settings(latest.second_order)
            save_settings(latest, SETTINGS_JSON_PATH)
            self._pa_settings.second_order = latest.second_order.model_copy(deep=True)
            self._status.setText("材料自动化设置已保存")
        except Exception as exc:  # noqa: BLE001
            self._status.setText(f"材料自动化设置保存失败：{exc}")

    def _set_news_prefetch_enabled(self, enabled: bool) -> None:
        self._update_material_timers()
        self._on_material_runtime_settings_changed()

    def _set_material_preanalysis_enabled(self, enabled: bool) -> None:
        self._update_material_timers()
        self._on_material_runtime_settings_changed()

    def _update_material_timers(self) -> None:
        if not hasattr(self, "_news_prefetch_timer"):
            return
        self._schedule_material_timer(
            self._news_prefetch_timer,
            self._news_prefetch_time.time(),
            self._run_scheduled_news_prefetch,
            self._news_prefetch_toggle.isChecked(),
        )
        self._schedule_material_timer(
            self._material_preanalysis_timer,
            self._material_preanalysis_time.time(),
            self._run_scheduled_material_preanalysis,
            self._material_preanalysis_toggle.isChecked(),
        )

    def _run_scheduled_news_prefetch(self) -> None:
        self._run_news_prefetch()
        QTimer.singleShot(1000, self._update_material_timers)

    def _run_scheduled_material_preanalysis(self) -> None:
        self._run_material_preanalysis()
        QTimer.singleShot(1000, self._update_material_timers)

    @staticmethod
    def _schedule_material_timer(
        timer: QTimer, target: QTime, callback: Any, enabled: bool
    ) -> None:
        timer.stop()
        if not enabled or not target.isValid():
            return
        now = QTime.currentTime()
        delay = now.msecsTo(target)
        if delay <= 0:
            delay += 24 * 60 * 60 * 1000
        timer.setSingleShot(True)
        try:
            timer.timeout.disconnect()
        except TypeError:
            pass
        timer.timeout.connect(callback)
        timer.start(delay)

    def _material_payload(self) -> dict[str, Any]:
        payload = dict(self._payload)
        payload.update(
            {
                "symbol": self._symbol.text().strip(),
                "sector_name": self._sector_name_edit.text().strip(),
                "sector_code": self._sector_code_edit.text().strip(),
                "dsa_database_path": self._dsa_database_edit.text().strip(),
                "run_news_prefetch_enabled": True,
                "run_material_preanalysis_enabled": True,
                "timeframe": "120m",
            }
        )
        return payload

    def _run_news_prefetch(self) -> None:
        sector = (
            self._sector_name_edit.text().strip()
            or str(self._payload.get("stock_name") or "").strip()
            or self._symbol.text().strip()
        )
        self._start_worker(
            "news_prefetch",
            {
                "symbol": self._symbol.text().strip(),
                "stock_name": self._payload.get("stock_name"),
                "sector_name": sector,
                "sector_code": self._sector_code_edit.text().strip(),
            },
        )

    def _run_material_preanalysis(self) -> None:
        if not self._symbol.text().strip():
            self._material_runtime_text.setPlainText("材料预分析未启动：品种为空。")
            return
        self._start_worker("material_preanalysis", self._material_payload())

    def _refresh_material_runtime(self) -> None:
        try:
            _adapter, service_type, _market = _load_second_order_modules()
            service = service_type(
                market_source=object(),
                model_client=object(),
            )
            status = service.material_cache_status()
            preview = service.material_cache_preview()
            news_details = service.material_cache_news()
        except Exception as exc:  # noqa: BLE001
            self._material_runtime_text.setPlainText(f"材料状态读取失败：{exc}")
            return
        self._render_material_runtime(
            {
                "ok": True,
                "cache": status,
                "preview": preview,
                "news_details": news_details,
            },
            operation="status",
        )

    def _render_material_runtime(
        self, value: Mapping[str, Any], *, operation: str
    ) -> None:
        self._populate_material_news_table(value.get("news_details"))
        cache = value.get("cache")
        cache = cache if isinstance(cache, Mapping) else {}
        preview = value.get("preview")
        preview = preview if isinstance(preview, Mapping) else {}
        payload = {
            "任务": "消息预取" if operation == "news_prefetch" else (
                "材料预分析" if operation == "material_preanalysis" else "状态刷新"
            ),
            "结果": value.get("status"),
            "任务状态": value.get("task"),
            "缓存生命周期": cache,
            "缓存类别": {
                str(category): len(items) if isinstance(items, Mapping) else 1
                for category, items in preview.items()
            },
            "预分析材料类别": sorted(
                str(key)
                for key in (value.get("materials") or {})
            ) if isinstance(value.get("materials"), Mapping) else [],
            "个股博弈信号状态": (
                value.get("game_signals") or {}
            ).get("status")
            if isinstance(value.get("game_signals"), Mapping)
            else "暂无",
            "错误": value.get("error"),
        }
        self._material_runtime_text.setPlainText(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        )

    def _toggle_raw_material_fields(self, checked: bool) -> None:
        """展开/收起材料缓存原始字段文本框（替代原「刷新状态」按钮）。"""
        if checked:
            self._refresh_material_runtime()
        self._material_runtime_text.setVisible(checked)

    def _populate_material_news_table(self, news_details: object) -> None:
        tree = self._material_news_table
        tree.clear()
        if not isinstance(news_details, Mapping):
            return
        for sector_name, detail in news_details.items():
            if not isinstance(detail, Mapping):
                continue
            sector_code = detail.get("sector_code")
            items = detail.get("items")
            if not isinstance(items, list | tuple):
                items = []
            sector_sum = self._format_news_score(detail.get("sentiment_sum"))
            sector_purpose = detail.get("subject_purpose")
            sector_row = QTreeWidgetItem(
                [
                    str(sector_name),
                    f"{len(items)} 条消息",
                    "",
                    "",
                    str(sector_code or ""),
                    "",
                    sector_sum,
                    "",
                    "",
                    "",
                    str(sector_purpose or ""),
                ]
            )
            font = sector_row.font(0)
            font.setBold(True)
            for column in range(tree.columnCount()):
                sector_row.setFont(column, font)
            tree.addTopLevelItem(sector_row)
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                code = item.get("code") or ()
                code_text = (
                    ", ".join(str(part) for part in code)
                    if code
                    else ""
                )
                child = QTreeWidgetItem(
                    [
                        "",
                        item.get("title"),
                        item.get("url"),
                        item.get("published_date"),
                        code_text,
                        item.get("source"),
                        self._format_news_score(item.get("sentiment_score")),
                        self._format_news_factor(item.get("relevance")),
                        self._format_news_factor(item.get("validity")),
                        self._format_news_factor(item.get("source_credibility")),
                        str(item.get("subject_purpose") or ""),
                    ]
                )
                if item.get("title"):
                    child.setToolTip(1, str(item.get("title") or ""))
                if item.get("url"):
                    child.setToolTip(2, str(item.get("url") or ""))
                child.setData(0, _NEWS_SNIPPET_ROLE, str(item.get("snippet") or ""))
                sector_row.addChild(child)
        tree.expandAll()
        for column in range(tree.columnCount()):
            tree.resizeColumnToContents(column)

    def _on_material_news_item_clicked(
        self, item: QTreeWidgetItem, _column: int
    ) -> None:
        """Toggle an inline read-only text box with the raw message snippet.

        Only message rows (children of a sector row) react; clicking the
        sector summary row or the inline original-text row itself is ignored.
        """
        parent = item.parent()
        if parent is None or parent.parent() is not None:
            return
        tree = self._material_news_table
        for index in range(item.childCount()):
            child = item.child(index)
            if child.data(0, _NEWS_ORIGINAL_ROW_ROLE):
                tree.removeItemWidget(child, 1)
                item.removeChild(child)
                return
        snippet = str(item.data(0, _NEWS_SNIPPET_ROLE) or "").strip()
        if not snippet:
            self._status.setText("该消息没有可展开的原文内容")
            return
        detail = QTreeWidgetItem()
        detail.setData(0, _NEWS_ORIGINAL_ROW_ROLE, True)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(snippet)
        editor.setMaximumHeight(160)
        editor.setStyleSheet(
            "QPlainTextEdit { background: #11161D; color: #E8ECF1; border: 1px solid #2A3039;"
            " border-radius: 4px; font-size: 14px; padding: 6px; }"
        )
        item.addChild(detail)
        tree.setItemWidget(detail, 1, editor)
        tree.expandItem(item)
        tree.scrollToItem(detail)

    @staticmethod
    def _format_news_score(value: object) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "—"
        return f"{float(value):+.3f}"

    @staticmethod
    def _format_news_factor(value: object) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "—"
        return f"{float(value):.3f}"

    @staticmethod
    def _materials_news_details(materials: Mapping[str, Any]) -> dict[str, Any]:
        """Build the same news-details shape the material-cache tab consumes."""
        if not isinstance(materials, Mapping):
            return {}
        news = materials.get("news")
        scored = materials.get("scored_news")
        subject_purpose_material = materials.get("subject_purpose")
        sector_analysis = materials.get("sector_analysis")
        sector_name = None
        sector_code = None
        if isinstance(sector_analysis, Mapping):
            sector_name = (
                sector_analysis.get("sector_name")
                or sector_analysis.get("news_keyword")
            )
            sector_code = sector_analysis.get("sector_code")
        news_items = news.get("items") if isinstance(news, Mapping) else None
        scored_items = scored.get("items") if isinstance(scored, Mapping) else None
        if not isinstance(news_items, list | tuple):
            news_items = []
        if not isinstance(scored_items, list | tuple):
            scored_items = []
        scored_by_title = {
            str(item.get("title") or ""): item
            for item in scored_items
            if isinstance(item, Mapping)
        }
        items: list[dict[str, Any]] = []
        sentiment_total: float | None = None
        for item in news_items:
            if not isinstance(item, Mapping):
                continue
            matched = scored_by_title.get(str(item.get("title") or ""), {})
            related = item.get("related_securities") or ()
            if isinstance(related, str):
                related = [related]
            score = matched.get("sentiment_score")
            if isinstance(score, int | float) and not isinstance(score, bool):
                sentiment_total = (
                    float(score) if sentiment_total is None else sentiment_total + float(score)
                )
            items.append(
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "published_date": _format_published_date_text(
                        item.get("published_date")
                    ),
                    "code": list(related),
                    "source": item.get("source"),
                    "snippet": item.get("snippet"),
                    "sentiment_score": score,
                    "relevance": matched.get("relevance"),
                    "validity": matched.get("validity"),
                    "source_credibility": matched.get("source_credibility"),
                    "subject_purpose": matched.get("subject_purpose"),
                }
            )
        if not items:
            return {}
        sector_purpose = None
        if isinstance(subject_purpose_material, Mapping):
            purpose = subject_purpose_material.get("true_purpose")
            if isinstance(purpose, str) and purpose.strip():
                sector_purpose = purpose.strip()
        return {
            str(sector_name or "当前品种"): {
                "sector_code": sector_code,
                "count": len(items),
                "sentiment_sum": sentiment_total,
                "subject_purpose": sector_purpose,
                "items": items,
            }
        }

    @classmethod
    def _game_signal_summary(cls, signals: object) -> object:
        if not isinstance(signals, Mapping):
            return "暂无数据"
        if signals.get("status") == "insufficient_data":
            return {"状态": "数据不足", "原因": signals.get("reason") or "K 线样本不足"}
        nash = signals.get("nash") if isinstance(signals.get("nash"), Mapping) else {}
        herd = signals.get("herd") if isinstance(signals.get("herd"), Mapping) else {}
        smart = signals.get("smart_money") if isinstance(signals.get("smart_money"), Mapping) else {}
        inst = signals.get("institutional_flow") if isinstance(signals.get("institutional_flow"), Mapping) else {}
        trap = signals.get("liquidity_trap") if isinstance(signals.get("liquidity_trap"), Mapping) else {}
        features = signals.get("features") if isinstance(signals.get("features"), Mapping) else {}

        def yes_no(value: object) -> str:
            return "是" if value is True else "否" if value is False else "—"

        def number(value: object) -> str:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return "—"
            return f"{float(value):.4g}"

        position = {"below": "带下方", "above": "带上方", "inside": "带内"}.get(
            str(nash.get("position") or ""), str(nash.get("position") or "—")
        )
        return {
            "纳什均衡带": {
                "中心": number(nash.get("center")),
                "上沿": number(nash.get("upper")),
                "下沿": number(nash.get("lower")),
                "价格位置": position,
            },
            "羊群行为": {
                "羊群买入": yes_no(herd.get("buying")),
                "羊群卖出": yes_no(herd.get("selling")),
                "RSI": number(herd.get("rsi")),
                "异常放量": yes_no(herd.get("volume_spike")),
            },
            "聪明钱指数": {"净流入为正": yes_no(smart.get("positive"))},
            "机构资金": {
                "吸筹": yes_no(inst.get("accumulation")),
                "派发": yes_no(inst.get("distribution")),
            },
            "流动性陷阱": {"上方陷阱": yes_no(trap.get("upper")), "下方陷阱": yes_no(trap.get("lower"))},
            "反向/动量/回归信号": {
                "逆势买入": yes_no(features.get("contrarian_buy")),
                "逆势卖出": yes_no(features.get("contrarian_sell")),
                "动量买入": yes_no(features.get("momentum_buy")),
                "动量卖出": yes_no(features.get("momentum_sell")),
                "回归买入": yes_no(features.get("nash_reversion_buy")),
                "回归卖出": yes_no(features.get("nash_reversion_sell")),
            },
        }

    def _fetch_kline(self) -> None:
        self._refresh_dsa_on_open()
        self._start_worker(
            "kline", {"symbol": self._symbol.text().strip(), "timeframe": "120m"}
        )

    def _refresh_dsa_on_open(self) -> None:
        try:
            market = _refresh_dsa_market_cache(self._pa_context, self._material_payload())
        except Exception as exc:  # noqa: BLE001
            market = {"status": "error", "reason": str(exc) or type(exc).__name__}
        self._render_market_analysis(
            market,
            policy_environment=self._payload.get("policy_environment"),
        )

    def _regenerate_market_analysis(self) -> None:
        if self._workers:
            self._status.setText("二阶后台任务正在运行")
            return
        self._start_worker(
            "market_refresh",
            {
                "dsa_database_path": self._dsa_database_edit.text().strip(),
            },
        )

    def _render_market_analysis(
        self,
        market: Mapping[str, Any],
        *,
        policy_environment: object,
    ) -> None:
        explanation = {
            "模块化大盘分析": market.get("display_sections"),
            "说明": market.get("reason"),
        }
        self._market.set_grouped_payload(
            [
                [
                    ("状态", market.get("status"), 1),
                    ("来源", market.get("source"), 1),
                ],
                [
                    ("DSA 数据日期", market.get("data_date"), 1),
                    ("本次决策日期", market.get("decision_date"), 1),
                ],
                [
                    ("模块化大盘分析说明", explanation, 4),
                ],
            ],
            _market_raw_projection(market),
        )

    @staticmethod
    def _policy_detection_summary(materials: Mapping[str, Any]) -> dict[str, object]:
        """Summarize the policy-environment detection for the sector card.

        The detection is produced by ``PolicyDetector`` in the production
        context (``materials["policy_detection"]``) and carries the evidence
        chain (hard ETF volume / soft news keywords / system notes) plus the
        multiplier group selected by the environment.  Only the evidence
        summaries and the chosen group are shown here; the full record stays
        available in the raw payload.
        """
        detection = materials.get("policy_detection")
        if not isinstance(detection, Mapping):
            return {"状态": "等待二阶推演识别"}
        environment = detection.get("environment") or "无干预"
        evidence = detection.get("evidence")
        evidence_summary: object = "暂无证据"
        if isinstance(evidence, (list, tuple)) and evidence:
            evidence_summary = [
                {
                    "渠道": item.get("channel"),
                    "指向环境": item.get("environment") or environment,
                    "摘要": item.get("summary"),
                }
                for item in evidence
                if isinstance(item, Mapping)
            ]
        multipliers = detection.get("multipliers")
        return {
            "状态": detection.get("status") or "detected",
            "检测环境": environment,
            "选用的 multiplier 组": (
                multipliers if isinstance(multipliers, Mapping) else None
            ),
            "证据链": evidence_summary,
        }

    def _run_analysis(self) -> None:
        """运行二阶推演入口：第一步先自动触发「获取数据」，K 线就绪后再正式推演。"""
        if self._workers:
            self._pending_analysis = True
            self._status.setText("已有二阶任务正在运行")
            return
        self._update_llm_trace([])
        # 第一步：先自动触发「获取数据」；K 线获取完成后由 _worker_finished 继续正式推演
        self._pending_analysis = True
        self._fetch_kline()

    def _start_analysis_worker(self) -> None:
        """启动正式二阶推演 worker（在 K 线获取完成后由 _worker_finished 调用）。"""
        payload = dict(self._payload)
        payload.update(
            {
                "symbol": self._symbol.text().strip(),
                "sector_name": self._sector_name_edit.text().strip(),
                "sector_code": self._sector_code_edit.text().strip(),
                "dsa_database_path": self._dsa_database_edit.text().strip(),
                "run_news_prefetch_enabled": True,
                "run_material_preanalysis_enabled": True,
                "timeframe": "120m",
                "user_context": list(self._chat_context),
            }
        )
        self._start_worker("analysis", payload)

    def _send_chat_context(self) -> None:
        text = self._chat_input.text().strip()
        if not text:
            return
        self._chat_context.append({"role": "user", "content": text})
        self._chat_transcript.appendPlainText(
            f"\n用户：{text}\n系统：正在使用二阶博弈独立上下文重新推演。"
        )
        self._chat_input.clear()
        self._run_analysis()

    def _clear_chat_context(self) -> None:
        self._chat_context.clear()
        self._chat_transcript.setPlainText(
            "二阶博弈独立对话已清空。PA 的其他分析上下文未受影响。"
        )

    def _start_worker(self, operation: str, payload: Mapping[str, Any]) -> None:
        if self._workers:
            if operation == "analysis":
                self._pending_analysis = True
            self._status.setText("二阶后台任务正在运行")
            return
        worker = _ApiWorker(self._pa_context, operation, payload, self)
        self._workers.add(worker)
        worker.succeeded.connect(self._worker_succeeded)
        worker.failed.connect(self._worker_failed)
        worker.progress.connect(self._worker_progress)
        worker.progress_event.connect(self._on_worker_progress_event)
        worker.finished.connect(lambda worker=worker: self._worker_finished(worker))
        status_text = {
            "kline": "正在获取 K 线…",
            "analysis": "正在运行生产编排器…",
            "news_prefetch": "正在预取板块消息…",
            "material_preanalysis": "正在预分析材料…",
            "market_refresh": "正在重新生成大盘分析…",
            "labeler_catchup": "正在补跑 OHLCV 标注器…",
        }.get(operation, "正在运行二阶任务…")
        self._status.setText(status_text)
        if operation not in {"market_refresh", "labeler_catchup"}:
            self.pipeline_reset_requested.emit()
            self._overview_flow.reset()
            self._overview_flow.set_status(0, "done")
            if operation == "analysis":
                self._overview_stream.clear()
            if operation == "kline":
                self.pipeline_step_changed.emit(0, "active")
            elif operation in {"news_prefetch", "material_preanalysis"}:
                self._overview_flow.set_status(1, "done")
                self.pipeline_step_changed.emit(1, "active")
            elif operation == "analysis":
                self.pipeline_step_changed.emit(0, "done")
                self.pipeline_step_changed.emit(1, "active")
                self._overview_flow.set_status(1, "active")
        self._fetch.setEnabled(False)
        self._run.setEnabled(False)
        self._market_regenerate.setEnabled(False)
        worker.start()

    def _worker_progress(self, operation: str, stage: str, status: str) -> None:
        index = self._FLOW_STAGE_INDEX.get(stage)
        if index is None:
            return
        self._overview_flow.set_status(index, status)
        if operation == "analysis" and stage == "settings" and status == "active":
            self._status.setText(
                "检查分析设置：正在生成 DSA 大盘材料，首次运行约需 1-3 分钟，请耐心等待…"
            )

    def _on_worker_progress_event(self, event: Mapping[str, Any]) -> None:
        self._overview_stream.append_event(event)

    def _worker_finished(self, worker: _ApiWorker) -> None:
        self._workers.discard(worker)
        if self._pending_analysis:
            self._pending_analysis = False
            QTimer.singleShot(0, self._start_analysis_worker)

    def _worker_succeeded(self, operation: str, value: object) -> None:
        self._fetch.setEnabled(True)
        self._run.setEnabled(True)
        self._market_regenerate.setEnabled(True)
        if operation == "market_refresh":
            if isinstance(value, Mapping):
                market = value.get("market")
                if isinstance(market, Mapping):
                    self._render_market_analysis(
                        market,
                        policy_environment=self._payload.get("policy_environment"),
                    )
            self._status.setText("大盘分析已重新生成")
            return
        if operation == "labeler_catchup":
            if isinstance(value, Mapping):
                self._labeler_status_card.set_status(value)
            self._status.setText("标注器状态已刷新")
            return
        if operation == "kline" and isinstance(value, Mapping):
            frame = _to_frame(value)
            self._chart.set_game_signal_series(
                list(value.get("game_signal_series") or ())
            )
            self._chart.set_frame_now(frame, fit_view=True)
            self._status.setText(f"已载入 {len(frame.bars)} 根 K_120M")
            self.pipeline_step_changed.emit(0, "done")
            return
        if operation in {"news_prefetch", "material_preanalysis"}:
            if isinstance(value, Mapping):
                self._render_material_runtime(value, operation=operation)
                if operation == "material_preanalysis" and "llm_trace" in value:
                    self._render_llm_trace(value)
                if not value.get("ok", True):
                    self._worker_failed(
                        operation,
                        str(value.get("error") or "二阶材料任务失败"),
                    )
                    return
            self.pipeline_step_changed.emit(1, "done")
            self._status.setText(
                "消息预取完成" if operation == "news_prefetch" else "材料预分析完成"
            )
            return
        self._status.setText("生产编排完成")
        if isinstance(value, Mapping):
            self._render_llm_trace(value)
            if not value.get("ok"):
                self._worker_failed(operation, str(value.get("error") or "二阶推演失败"))
                return
            for index in range(1, 5):
                self.pipeline_step_changed.emit(index, "done")
            self._render_analysis_result(value)

    def _render_analysis_result(
        self, envelope: Mapping[str, Any], *, refresh_history: bool = True
    ) -> None:
        self._last_result_envelope = dict(envelope)
        result = envelope.get("result")
        result = result if isinstance(result, Mapping) else {}
        input_ = result.get("input")
        input_ = input_ if isinstance(input_, Mapping) else {}
        tree = result.get("scenario_tree")
        tree = tree if isinstance(tree, Mapping) else {}
        branches = list(tree.get("branches") or ())
        first = branches[0] if branches and isinstance(branches[0], Mapping) else {}
        gates = result.get("integrated_gates")
        gates = gates if isinstance(gates, Mapping) else {}
        participant_priors = {}
        participant_posteriors = {}
        materials = input_.get("materials")
        if isinstance(materials, Mapping):
            participant_priors = materials.get("participant_priors") or {}
            participant_posteriors = materials.get("participant_posteriors") or {}
        else:
            materials = {}
        metadata = tree.get("analysis_metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        participant_analysis = metadata.get("participant_analysis")
        participant_analysis = (
            participant_analysis if isinstance(participant_analysis, Mapping) else {}
        )
        participant = participant_analysis.get("participant") or "无法判断"
        primary_forecast = {}
        a_class = first.get("a_class")
        if isinstance(a_class, Mapping):
            candidate = a_class.get(participant)
            if not isinstance(candidate, Mapping) and a_class:
                candidate = next(iter(a_class.values()))
            primary_forecast = candidate if isinstance(candidate, Mapping) else {}
        primary_behavior = primary_forecast.get("model_behavior")
        if not primary_behavior:
            probabilities = primary_forecast.get("probabilities")
            if isinstance(probabilities, Mapping) and probabilities:
                primary_behavior = max(probabilities, key=probabilities.get)
        gate_statuses = [
            str(gate.get("status") or "")
            for gate in gates.values()
            if isinstance(gate, Mapping)
        ]
        if gate_statuses and all(status == "passed" for status in gate_statuses):
            gate_summary = "三种情景均通过 T+1 闸门"
        elif "insufficient_data" in gate_statuses:
            gate_summary = "数据不足，禁止新增买入"
        elif gate_statuses:
            passed = sum(status == "passed" for status in gate_statuses)
            gate_summary = f"{passed}/{len(gate_statuses)} 个情景通过，其余情景限制新增买入"
        else:
            gate_summary = "暂无可用的 T+1 闸门结论"
        gate_detail = self._gate_detail_reason(branches, materials, gates)
        gate_display = self._gate_display(branches, materials, gates, gate_detail)
        analysis_target = (
            str(self._payload.get("stock_name") or "").strip()
            or input_.get("symbol")
            or self._symbol.text().strip()
        )
        scenario_statuses = [
            {
                "情景": item.get("name"),
                "概率": self._scenario_period_probability(item),
                "状态": item.get("status"),
            }
            for item in branches
            if isinstance(item, Mapping)
        ]
        self._overview.set_grouped_payload(
            [
                [
                    ("分析对象", analysis_target, 1),
                    (
                        "决策基准",
                        self._decision_point_text(input_.get("decision_point")),
                        1,
                    ),
                ],
                [
                    ("情绪周期", input_.get("cycle_position"), 1),
                    ("主导参与者", participant, 1),
                    ("可能行为", primary_behavior or "无法判断", 1),
                ],
                [("关键证据", participant_analysis.get("key_evidence"), 1)],
                [("三情景状态", scenario_statuses, 1)],
                [("T+1 综合结论", gate_summary, 1)],
                [("分析完成时间", result.get("completed_at"), 1)],
            ],
            result,
        )
        self._game_reasoning.set_payload(
            {
                "程序化博弈信号": self._game_signal_summary(input_.get("game_signals")),
                "参与者识别": participant_analysis,
                "参与者先验": participant_priors,
                "参与者后验": participant_posteriors,
                "主导参与者行为推演": first.get("a_class"),
            },
            {
                "game_signals": input_.get("game_signals"),
                "participant_analysis": participant_analysis,
                "participant_priors": participant_priors,
                "participant_posteriors": participant_posteriors,
                "a_class": first.get("a_class"),
                "probability_chain": tree.get("probability_chain"),
                "branches": branches,
                "pa_metrics": result.get("pa_metrics"),
            },
        )
        self._tree.set_payload(
            {
                "B/C三情景概率": [
                    {
                        "情景": item.get("name"),
                        "下一完整时段概率": self._scenario_period_probability(item),
                        "开盘首次下跌达止损概率": self._stop_first_probability(item),
                        "状态": item.get("status"),
                        "应对": item.get("action_advice") or "暂无可执行动作",
                    }
                    for item in branches
                    if isinstance(item, Mapping)
                ],
            },
            branches,
        )
        self._gate.set_grouped_payload(
            [
                [("T+1 结论", gate_display["结论"], 1)],
                [("新增买入", gate_display["新增买入"], 1)],
                [("缺少的前置条件", gate_display["原因"], 1)],
                [("下一步", gate_display["下一步"], 1)],
            ],
            {"integrated_gates": dict(gates), "position_cases": materials.get("position_cases")},
        )
        sector_analysis = materials.get("sector_analysis")
        sector_analysis = sector_analysis if isinstance(sector_analysis, Mapping) else {}
        sentiment_details = sector_analysis.get("sentiment_index_details")
        sentiment_details = sentiment_details if isinstance(sentiment_details, Mapping) else {}
        sentiment_display = _sentiment_index_display(
            sector_analysis.get("sentiment_index"), sentiment_details
        )
        self._cycle.set_grouped_payload(
            [
                [
                    ("情绪指数", sentiment_display, 1),
                    ("情绪指数计算公式", sentiment_details.get("formula"), 2),
                ],
                [("情绪指数明细", sentiment_details, 2)],
                [("LLM 周期观测", materials.get("cycle_observation"), 2)],
                [("HMM 后验信念", input_.get("sector_belief"), 2)],
            ],
            {
                "sentiment_index": sector_analysis.get("sentiment_index"),
                "sentiment_index_details": sentiment_details,
                "cycle_observation": materials.get("cycle_observation"),
                "sector_belief": input_.get("sector_belief"),
            },
        )
        self._sector.set_grouped_payload(
            [
                [("板块结构", materials.get("sector_analysis"), 3)],
                [
                    (
                        "政策环境",
                        input_.get("policy_environment") or "等待二阶推演识别",
                        1,
                    ),
                    (
                        "政策检测",
                        self._policy_detection_summary(materials),
                        2,
                    ),
                ],
            ],
            {
                "sector_analysis": materials.get("sector_analysis"),
                "news": materials.get("news"),
                "policy_environment": input_.get("policy_environment"),
                "policy_detection": materials.get("policy_detection"),
            },
        )
        sector_bundle = envelope.get("sector_analysis_bundle")
        if isinstance(sector_bundle, Mapping):
            self._sector.set_table_sections(_sector_bundle_sections(sector_bundle))
        market_analysis = materials.get("market_analysis")
        market_analysis = market_analysis if isinstance(market_analysis, Mapping) else {}
        self._render_market_analysis(
            market_analysis,
            policy_environment=input_.get("policy_environment"),
        )
        news_details = envelope.get("news_details")
        if not isinstance(news_details, Mapping):
            news_details = self._materials_news_details(materials)
        self._populate_material_news_table(news_details)
        self._material_runtime_text.setPlainText(
            json.dumps(
                {
                    "生命周期": materials.get("material_cache"),
                    "本次材料类别": sorted(
                        str(key)
                        for key in materials
                        if key != "material_snapshot"
                    ),
                    "大盘材料状态": (
                        materials.get("market_analysis") or {}
                    ).get("status")
                    if isinstance(materials.get("market_analysis"), Mapping)
                    else "不可用",
                    "板块材料状态": "已准备" if materials.get("sector_analysis") else "暂无",
                    "消息材料状态": (
                        materials.get("news") or {}
                    ).get("status")
                    if isinstance(materials.get("news"), Mapping)
                    else "暂无",
                    "个股博弈信号状态": (
                        input_.get("game_signals") or {}
                    ).get("status")
                    if isinstance(input_.get("game_signals"), Mapping)
                    else "暂无",
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        if "llm_trace" in envelope:
            self._render_llm_trace(envelope)
        if refresh_history:
            self._refresh_history()
            self._chat_transcript.appendPlainText(
                "\n二阶博弈：\n" + self._assistant_context(first, gates)
            )
            self._chat_context.append(
                {"role": "assistant", "content": self._assistant_context(first, gates)}
            )

    @staticmethod
    def _status_text(value: object) -> str:
        return {
            "passed": "闸门通过",
            "blocked": "闸门未通过",
            "insufficient_data": "数据不足，暂不评估新增买入",
            "not_applicable": "不适用",
        }.get(str(value or ""), "暂无结论")

    @classmethod
    def _scenario_period_probability(cls, branch: Mapping[str, Any]) -> str:
        values = branch.get("b_class")
        if not isinstance(values, Mapping):
            return "暂无数据"
        outcome = {
            "超预期强": "gap_up",
            "符合预期": "near_reference",
            "低于预期": "gap_down",
        }.get(str(branch.get("name") or ""))
        return cls._probability_text(values.get(outcome))

    @classmethod
    def _stop_first_probability(cls, branch: Mapping[str, Any]) -> str:
        values = branch.get("c_class")
        return cls._probability_text(
            values.get("stop_first") if isinstance(values, Mapping) else None
        )

    @staticmethod
    def _probability_text(value: object) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "暂无数据"
        return f"{float(value) * 100:.1f}%"

    @classmethod
    def _gate_detail_reason(
        cls,
        branches: list[object],
        materials: Mapping[str, Any],
        gates: Mapping[str, Any],
    ) -> str:
        probability_chain = materials.get("probability_chain")
        if isinstance(probability_chain, Mapping):
            reason = str(probability_chain.get("reason") or "").strip()
            if reason:
                return reason
        for item in branches:
            if isinstance(item, Mapping):
                reason = str(item.get("gate_reason") or "").strip()
                if reason:
                    return reason
        for item in gates.values():
            if isinstance(item, Mapping):
                reason = str(item.get("reason") or "").strip()
                if reason:
                    return reason
        return "暂无明确的二阶闸门原因"

    @classmethod
    def _gate_display(
        cls,
        branches: list[object],
        materials: Mapping[str, Any],
        gates: Mapping[str, Any],
        detail_reason: str,
    ) -> dict[str, str]:
        statuses = [
            str(item.get("status") or "")
            for item in gates.values()
            if isinstance(item, Mapping)
        ]
        insufficient = "insufficient_data" in statuses or any(
            isinstance(item, Mapping) and item.get("status") == "insufficient_data"
            for item in branches
        )
        pa_blocked = any(
            isinstance(item, Mapping) and item.get("pa_gate_passed") is False
            for item in gates.values()
        )
        pa_no_signal = pa_blocked and any(
            isinstance(item, Mapping)
            and item.get("status") == "not_applicable"
            and item.get("reason") == "没有下单信号，T+1新增买入暂不评估"
            for item in gates.values()
        )
        missing = detail_reason
        if pa_no_signal:
            conclusion = "没有下单信号，T+1新增买入暂不评估"
            second_order = "PA 未产生下单信号，未进入新增买入闸门评估"
            next_step = "等待技术分析产生下单信号后，再评估 T+1 新增买入"
            missing = "没有下单信号，T+1新增买入暂不评估"
        elif insufficient:
            conclusion = "二阶数据不足，T+1 新增买入不评估"
            second_order = "未完成 B/C 概率闸门计算"
            if "缺少止盈价或止损价" in detail_reason:
                next_step = "让 PA 阶段 2 提供止盈价和止损价后，重新运行二阶推演。"
            elif "有效的正收益与负风险区间" in detail_reason:
                next_step = "检查 PA 入场、止盈、止损的方向与价格关系后重新运行。"
            else:
                next_step = "等待积累足够的匹配历史样本后重新评估。"
        elif statuses and all(status == "passed" for status in statuses):
            conclusion = "PA 与二阶 T+1 闸门均通过"
            second_order = "B/C 概率可用"
            next_step = "应对树：按实际出现的情景分支执行对应动作。"
            missing = "无"
        else:
            conclusion = "T+1 新增买入被阻断"
            second_order = "二阶闸门未通过"
            next_step = "检查闸门原因并等待新的有效数据。"
            missing = detail_reason
        return {
            "结论": conclusion,
            "原因": missing,
            "影响范围": "同一个二阶闸门结果同时作用于三个情景；这不是三份独立数据缺失。",
            "下一步": next_step,
            "PA 闸门": "未通过：PA 阶段 2 should_trade=false" if pa_blocked else "已通过",
            "二阶数据": second_order,
            "新增买入": "不评估" if pa_no_signal else ("阻断" if insufficient or pa_blocked or conclusion != "PA 与二阶 T+1 闸门均通过" else "允许执行"),
        }

    @staticmethod
    def _assistant_context(first: Mapping[str, Any], gates: Mapping[str, Any]) -> str:
        return json.dumps(
            {"参与者与行为": first.get("a_class"), "T+1闸门": dict(gates)},
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def _worker_failed(self, operation: str, message: str) -> None:
        self._fetch.setEnabled(True)
        self._run.setEnabled(True)
        display_message = self._worker_error_message(message)
        self._market_regenerate.setEnabled(True)
        if operation == "market_refresh":
            self._status.setText(f"大盘分析重新生成失败：{display_message}")
            QMessageBox.warning(self, "大盘分析", display_message)
            return
        failed_index = self._overview_flow.active_index
        if failed_index is None:
            failed_index = {"kline": 0, "news_prefetch": 3, "material_preanalysis": 2, "analysis": 2}.get(operation, 0)
        self._overview_flow.set_status(failed_index, "error", display_message)
        self._status.setText(f"失败：{display_message}")
        failed_step = {
            "kline": 0,
            "news_prefetch": 1,
            "material_preanalysis": 1,
            "analysis": 2,
        }.get(operation, 0)
        self.pipeline_step_changed.emit(failed_step, "error")
        QMessageBox.warning(self, "二阶博弈", display_message)

    @staticmethod
    def _worker_error_message(message: str) -> str:
        if "缺少必填" in message and "sector_code" in message:
            return (
                f"{message}\n\n"
                "请打开“二阶博弈 → 设置”，填写富途返回的板块代码，点击“保存设置”后重试。"
            )
        return message

    def _refresh_history(self) -> None:
        try:
            _adapter, service_type, _market = _load_second_order_modules()
            symbol = self._symbol.text().strip()
            records = service_type.list_history(symbol, 30)
            history_summary = service_type.history_summary(symbol)
        except Exception as exc:  # noqa: BLE001
            self._history_text.setPlainText(f"历史记录读取失败：{exc}")
            self._history_calibration.set_summary(None)
            return
        self._history_records = {int(item["id"]): item for item in records}
        self._history_table.setRowCount(len(records))
        labels = {"win": "胜出", "loss": "失效", "neutral": "持平"}
        for row, item in enumerate(records):
            values = (
                item.get("id"), item.get("symbol"), item.get("sector_name"),
                item.get("decision_point"), item.get("completed_at"),
                labels.get(item.get("actual_result"), "待结算"),
            )
            for column, value in enumerate(values):
                self._history_table.setItem(row, column, QTableWidgetItem(str(value or "")))
        self._history_table.resizeColumnsToContents()
        resolved = int(history_summary.get("resolved") or 0)
        rate = history_summary.get("win_rate")
        rate_text = f"{float(rate) * 100:.1f}%" if rate is not None else "样本不足"
        self._history_summary_label.setText(
            f"记录 {history_summary.get('total', 0)} 条 | 已结算 {resolved} | "
            f"胜 {history_summary.get('wins', 0)} / 负 {history_summary.get('losses', 0)} / "
            f"持平 {history_summary.get('neutral', 0)} | 胜率 {rate_text}"
        )
        self._history_calibration.set_summary(history_summary.get("calibration"))
        if records:
            self._history_table.selectRow(0)
        else:
            self._history_text.setPlainText("暂无历史分析记录。")

    def _toggle_history_raw_stream(self, checked: bool) -> None:
        """展开/收起历史记录的后端数据流文本框（与「回放推演过程」并列）。"""
        self._history_text.setVisible(checked)

    def _history_selection_changed(self) -> None:
        row = self._history_table.currentRow()
        item = self._history_table.item(row, 0) if row >= 0 else None
        if item is None:
            return
        record_id = int(item.text())
        self._history_id_spin.setValue(record_id)
        record = getattr(self, "_history_records", {}).get(record_id, {})
        self._history_text.setPlainText(
            json.dumps(record.get("result", {}), ensure_ascii=False, indent=2, default=str)
        )

    def _open_selected_history(self) -> None:
        record = getattr(self, "_history_records", {}).get(self._history_id_spin.value())
        if not isinstance(record, Mapping):
            return
        result = record.get("result", {})
        self._render_analysis_result(
            {"ok": True, "result": result}, refresh_history=False
        )
        self._tabs.setCurrentWidget(self._game_tab)
        self._game_tabs.setCurrentWidget(self._game_reasoning)

    def _replay_history(self) -> None:
        record = getattr(self, "_history_records", {}).get(self._history_id_spin.value())
        if not isinstance(record, Mapping):
            self._history_text.setPlainText("请先选择一条历史记录。")
            return
        result = record.get("result", {})
        events = result.get("progress_events") if isinstance(result, Mapping) else None
        if not isinstance(events, list) or not events:
            self._history_text.setPlainText("该记录未保存推演过程（可能是旧版本生成）。")
            return
        self._overview_stream.set_replay(events)
        self._tabs.setCurrentWidget(self._overview_tab)

    def _resolve_history(self, outcome: str) -> None:
        record_id = self._history_id_spin.value()
        if record_id < 1:
            self._history_text.setPlainText("请先输入要结算的历史记录编号。")
            return
        try:
            _adapter, service_type, _market = _load_second_order_modules()
            service_type.resolve_history(record_id, outcome)
        except Exception as exc:  # noqa: BLE001
            self._history_text.setPlainText(f"历史记录结算失败：{exc}")
            return
        self._refresh_history()

    def _show_history_context_menu(self, pos) -> None:
        index = self._history_table.indexAt(pos)
        if not index.isValid():
            return
        menu = QMenu(self)
        delete_action = menu.addAction("删除记录")
        chosen = menu.exec(self._history_table.viewport().mapToGlobal(pos))
        if chosen == delete_action:
            self._delete_selected_history(index.row())

    def _delete_selected_history(self, row: int) -> None:
        item = self._history_table.item(row, 0)
        if item is None:
            return
        record_id = int(item.text())
        record = getattr(self, "_history_records", {}).get(record_id, {})
        symbol = str(record.get("symbol") or "").strip()
        decision_point = str(record.get("decision_point") or "").strip()
        reply = QMessageBox.question(
            self,
            "删除历史记录",
            f"确定删除记录 #{record_id}（{symbol} · {decision_point}）？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            _adapter, service_type, _market = _load_second_order_modules()
            service_type.delete_history(record_id)
        except Exception as exc:  # noqa: BLE001
            self._history_text.setPlainText(f"删除历史记录失败：{exc}")
            return
        self._history_text.setPlainText(f"已删除记录 #{record_id}。")
        self._refresh_history()

    def _user_experience_path(self) -> Path:
        return second_order_root() / "prompt_engine" / "通用" / "用户经验.txt"

    def _open_user_experience_editor(self) -> None:
        path = self._user_experience_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("", encoding="utf-8")
            current = path.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "无法打开", f"用户经验文件读取失败：{exc}")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("导入经验 — 用户经验")
        dialog.resize(640, 480)
        layout = QVBoxLayout(dialog)
        hint = QLabel(
            "在此填写你的交易经验。保存后，每一轮分析会将其注入大模型"
            "（新闻情绪评分、博弈推演、情景应对、主体目的分析）。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9AA5B1;")
        layout.addWidget(hint)
        editor = QPlainTextEdit()
        editor.setPlainText(current)
        layout.addWidget(editor, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        save_btn = QPushButton("保存")
        cancel_btn = QPushButton("取消")
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

        def _save() -> None:
            try:
                path.write_text(editor.toPlainText(), encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(dialog, "保存失败", str(exc))
                return
            self._status.setText("用户经验已保存")
            dialog.accept()

        save_btn.clicked.connect(_save)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec()

    def _open_import_news_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("导入消息")
        dialog.resize(600, 480)
        layout = QVBoxLayout(dialog)
        hint = QLabel(
            "粘贴新闻正文，首行前 15 个字自动作为标题。导入后将置顶到当前板块的预分析列表。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9AA5B1;")
        layout.addWidget(hint)
        form = QFormLayout()
        content_edit = QPlainTextEdit()
        content_edit.setPlaceholderText("在此粘贴新闻正文（全部作为正文）")
        form.addRow("正文", content_edit)
        title_edit = QLineEdit()
        title_edit.setPlaceholderText("自动取首行前 15 字，可手动修改")
        form.addRow("标题", title_edit)
        time_edit = QLineEdit()
        time_edit.setText(datetime.now().strftime("%Y-%m-%d %H:%M"))
        time_edit.setPlaceholderText("YYYY-MM-DD HH:MM")
        form.addRow("时间", time_edit)
        source_edit = QLineEdit()
        source_edit.setPlaceholderText("例如 用户导入 / 财联社")
        form.addRow("来源", source_edit)
        layout.addLayout(form)

        def _sync_title() -> None:
            if title_edit.text().strip():
                return
            for line in content_edit.toPlainText().splitlines():
                if line.strip():
                    title_edit.setText(line.strip()[:15])
                    break

        content_edit.textChanged.connect(_sync_title)

        buttons = QHBoxLayout()
        buttons.addStretch()
        submit_btn = QPushButton("导入")
        cancel_btn = QPushButton("取消")
        buttons.addWidget(cancel_btn)
        buttons.addWidget(submit_btn)
        layout.addLayout(buttons)

        def _submit() -> None:
            snippet = content_edit.toPlainText().strip()
            if not snippet:
                QMessageBox.warning(dialog, "无法导入", "请先粘贴新闻正文")
                return
            sector = (
                self._sector_name_edit.text().strip()
                or str(self._payload.get("stock_name") or "").strip()
                or self._symbol.text().strip()
            )
            if not sector:
                QMessageBox.warning(dialog, "无法导入", "请先在设置页填写关联板块名称")
                return
            payload = {
                "sector_name": sector,
                "title": title_edit.text().strip(),
                "snippet": snippet,
                "published_date": time_edit.text().strip(),
                "source": source_edit.text().strip(),
            }
            try:
                _adapter, service_type, _market = _load_second_order_modules()
                service = service_type(market_source=object(), model_client=object())
                result = service.import_news(payload)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(dialog, "导入失败", str(exc))
                return
            if not isinstance(result, Mapping) or not result.get("ok"):
                reason = (
                    result.get("error")
                    if isinstance(result, Mapping)
                    else "导入未返回有效结果"
                )
                QMessageBox.warning(dialog, "导入失败", str(reason))
                return
            dialog.accept()
            self._status.setText("消息已导入并置顶")
            self._refresh_material_runtime()

        submit_btn.clicked.connect(_submit)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.exec()

    def _open_resource(self, resource: str) -> None:
        try:
            root = second_order_root()
            if resource == "hmm":
                import subprocess

                subprocess.Popen(
                    [sys.executable, "-m", "src.gui.workbench"],
                    cwd=str(root),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return
            targets = {
                "config": root / "config",
                "prompts": root / "prompt_engine",
                "history": root / "analysis_history",
            }
            target = targets.get(resource)
            if target is None:
                raise ValueError(f"未知资源：{resource}")
            if resource == "history":
                target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "无法打开", str(exc))

    def shutdown(self, timeout_ms: int = 10_000) -> None:
        """Stop timers and wait for owned Qt workers before widget teardown."""
        if hasattr(self, "_news_prefetch_timer"):
            self._news_prefetch_timer.stop()
            self._material_preanalysis_timer.stop()
        deadline = max(0, int(timeout_ms))
        for worker in tuple(self._workers):
            worker.requestInterruption()
            worker.wait(deadline)


__all__ = ["SecondOrderWorkspace", "second_order_root"]
