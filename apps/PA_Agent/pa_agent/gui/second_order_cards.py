"""Prototype-styled analysis cards for the SecondOrderGame workspace.

Renders the five analysis tabs (情绪周期 / 博弈推演 / 应对树 / 板块分析 /
大盘分析) with the visual language of ``second_order_tabs_prototype.html``:
dark restrained panels, hairline separators, proportional bars and a muted
numeric palette.  Red = up / green = down (A-share convention).

``PrototypeAnalysisPanel`` subclasses the existing ``_AnalysisResultPanel`` so
the backend call sites (``set_payload`` / ``set_grouped_payload`` /
``set_table_sections`` / ``set_title`` / ``add_header_action``) keep working
unchanged; only the rendering layer is replaced.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
)

from pa_agent.gui.second_order_workspace import _AnalysisResultPanel

# ---------------------------------------------------------------------------
# Palette (aligned with the prototype + existing PA dark theme)
# ---------------------------------------------------------------------------
_BG = "#151920"
_PANEL = "#11161D"
_PANEL_RAISED = "#11161D"
_PANEL_SOFT = "#20252D"
_LINE = "#2A3039"
_LINE_STRONG = "#3A4250"
_TEXT = "#E8ECF1"
_TEXT_2 = "#B6BDC8"
_TEXT_3 = "#7F8A99"
_ACCENT = "#6F99D5"
_ACCENT_SOFT = "#1E2A3C"
_UP = "#EF6670"      # 红涨
_DOWN = "#35BC88"    # 绿跌
_WARN = "#D3A64C"
_SEG_IDLE = "#53606F"

_FONT_UI = '"Microsoft YaHei UI", "Segoe UI", sans-serif'
_FONT_NUM = '"Cascadia Mono", "Consolas", monospace'
_FONT_HEITI = '"SimHei", "Microsoft YaHei UI", "Segoe UI", sans-serif'


def _num(value: object, digits: int = 1) -> str:
    """Render a number compactly, or an em-dash when unusable.

    Accepts strings that parse as numbers (e.g. ``"1.404"``) so callers can
    forward pre-formatted values from game-signal summaries without round
    tripping through the original numeric type.
    """
    if isinstance(value, bool):
        return "—"
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}f}"
    if isinstance(value, str):
        try:
            return f"{float(value):.{digits}f}"
        except ValueError:
            return value or "—"
    return "—"


def _pct(value: object) -> str:
    """Render a probability (0-1) as a percentage string."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{float(value) * 100:.1f}%"


def _strip_number(text: object) -> str:
    """Pull the leading number out of display strings like 休市（保持值 50.0）."""
    import re

    if text is None or text == "":
        return "—"
    match = re.search(r"-?\d+(?:\.\d+)?", str(text))
    return match.group(0) if match else str(text)


def _card_qss(kind: str) -> str:
    base = (
        f"QFrame#{kind} {{ background: {_PANEL}; border: 1px solid {_LINE}; "
        f"border-radius: 4px; }}"
    )
    return base


def _clean_markdown_text(value: object) -> str:
    """Remove Markdown chrome while preserving the analyst's wording."""
    text = str(value or "").strip()
    text = re.sub(r"^[>\s]+", "", text)
    text = re.sub(r"[*_`~]", "", text)
    text = re.sub(r"[\ufe0f\u200d]", "", text)
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_section_title(value: object) -> str:
    title = _clean_markdown_text(value)
    return re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", title).strip()


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _markdown_blocks(content: object) -> dict[str, Any]:
    """Parse the DSA Markdown subset into paragraphs, facts, lists and tables."""
    lines = str(content or "").replace("\r\n", "\n").split("\n")
    result: dict[str, Any] = {
        "paragraphs": [],
        "facts": [],
        "lists": [],
        "tables": [],
    }
    heading = ""
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            text = _clean_markdown_text(" ".join(paragraph))
            if text:
                result["paragraphs"].append((heading, text))
            paragraph.clear()

    index = 0
    while index < len(lines):
        raw = lines[index].strip()
        if not raw:
            flush_paragraph()
            index += 1
            continue
        heading_match = re.match(r"^#{1,6}\s+(.+)$", raw)
        if heading_match:
            flush_paragraph()
            heading = _clean_section_title(heading_match.group(1))
            index += 1
            continue
        if raw.startswith("|") and index + 1 < len(lines) and _is_table_separator(lines[index + 1]):
            flush_paragraph()
            headers = [_clean_markdown_text(cell) for cell in raw.strip("|").split("|")]
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(
                    [_clean_markdown_text(cell) for cell in lines[index].strip().strip("|").split("|")]
                )
                index += 1
            result["tables"].append({"title": heading, "headers": headers, "rows": rows})
            continue
        item_match = re.match(r"^(?:[-+*]|\d+[.、])\s+(.+)$", raw)
        if item_match:
            flush_paragraph()
            item = item_match.group(1).strip()
            fact_match = re.match(r"^\*\*(.+?)\*\*\s*[：:]\s*(.+)$", item)
            if fact_match:
                result["facts"].append(
                    (_clean_markdown_text(fact_match.group(1)), _clean_markdown_text(fact_match.group(2)))
                )
            else:
                result["lists"].append((heading, _clean_markdown_text(item)))
            index += 1
            continue
        paragraph.append(raw)
        index += 1
    flush_paragraph()
    return result


class _TwoColumnGrid(QWidget):
    """Small placement helper that never renders more than two cards per row."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.layout_ = QGridLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setHorizontalSpacing(10)
        self.layout_.setVerticalSpacing(10)
        self.layout_.setColumnStretch(0, 1)
        self.layout_.setColumnStretch(1, 1)
        self._row = 0
        self._column = 0

    def add(self, widget: QWidget, *, span: int = 1) -> None:
        span = 2 if span >= 2 else 1
        if span == 2:
            if self._column:
                self._row += 1
                self._column = 0
            self.layout_.addWidget(widget, self._row, 0, 1, 2)
            self._row += 1
            return
        self.layout_.addWidget(widget, self._row, self._column)
        self._column += 1
        if self._column == 2:
            self._row += 1
            self._column = 0


class _Card(QFrame):
    """Base card with a title row and a content area."""

    def __init__(
        self,
        title: str = "",
        *,
        object_name: str = "protoCard",
        font_family: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inherited_font = f" font-family: {font_family};" if font_family else ""
        self.setStyleSheet(
            f"QFrame#{object_name} {{ background: {_PANEL_RAISED}; "
            f"border: 1px solid {_LINE}; border-radius: 4px;{inherited_font} }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)
        if title:
            head = QLabel(title)
            head.setObjectName("secondOrderFieldName")
            head.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT}; "
                "font-size: 16px; font-weight: 600;"
            )
            root.addWidget(head)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        root.addLayout(self.body)


class _FactGrid(QWidget):
    """Grid of label/value facts (prototype .fact-grid)."""

    def __init__(
        self,
        fields: list[tuple[str, object]],
        columns: int = 2,
        *,
        font_family: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(7)
        for index, (label, value) in enumerate(fields):
            card = QFrame()
            card.setObjectName("protoFact")
            card.setStyleSheet(
                f"QFrame#protoFact {{ background: {_PANEL_RAISED}; border: none; "
                "border-radius: 3px; }"
            )
            box = QVBoxLayout(card)
            box.setContentsMargins(9, 8, 9, 9)
            box.setSpacing(3)
            name = QLabel(str(label))
            name.setObjectName("secondOrderFieldName")
            name_font = f" font-family: {font_family};" if font_family else ""
            name.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT_3}; font-size: 14px;{name_font}"
            )
            value_label = QLabel(self._render(value))
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value_label.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT}; "
                f"font-family: {font_family or _FONT_NUM}; font-size: 15px; line-height: 1.35;"
            )
            box.addWidget(name)
            box.addWidget(value_label)
            grid.addWidget(card, index // columns, index % columns)

    @staticmethod
    def _render(value: object) -> str:
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value == int(value):
                return str(int(value))
            return f"{value:g}"
        if isinstance(value, Mapping):
            return "；".join(f"{k}：{v}" for k, v in value.items())
        return str(value or "—")


class _SummaryBand(QFrame):
    """Big numeric score with a copy block (prototype .summary-band)."""

    def __init__(
        self,
        value: object,
        label: str = "情绪指数",
        status_text: str = "",
        note: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("protoSummaryBand")
        self.setStyleSheet(
            f"QFrame#protoSummaryBand {{ background: {_PANEL}; "
            f"border: 1px solid {_LINE_STRONG}; border-radius: 4px; }}"
        )
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        score = QFrame()
        score.setObjectName("protoSummaryScore")
        score.setStyleSheet(
            f"QFrame#protoSummaryScore {{ background: {_ACCENT_SOFT}; "
            "border: none; border-top-left-radius: 3px; border-bottom-left-radius: 3px; }"
        )
        score_box = QVBoxLayout(score)
        score_box.setContentsMargins(14, 12, 14, 12)
        score_box.setSpacing(6)
        big = QLabel(_strip_number(value))
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        big.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT}; "
            f"font-family: {_FONT_NUM}; font-size: 32px; font-weight: 650;"
        )
        score_label = QLabel(label)
        score_label.setObjectName("secondOrderFieldName")
        score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        score_label.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT_2}; font-size: 14px;"
        )
        score_box.addWidget(big)
        score_box.addWidget(score_label)
        score.setFixedWidth(150)
        root.addWidget(score)
        copy = QWidget()
        copy_box = QVBoxLayout(copy)
        copy_box.setContentsMargins(14, 10, 14, 10)
        copy_box.setSpacing(4)
        status = QLabel(status_text or "已计算")
        status.setWordWrap(True)
        status.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT}; font-size: 16px; font-weight: 600;"
        )
        copy_box.addWidget(status)
        if note:
            note_label = QLabel(note)
            note_label.setWordWrap(True)
            note_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            note_label.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT_2}; font-size: 14px;"
            )
            copy_box.addWidget(note_label)
        copy_box.addStretch(1)
        root.addWidget(copy, 1)


class _BeliefBar(QWidget):
    """Five-segment posterior belief bar with a legend (prototype .belief-bar)."""

    _ORDER_HINT = ("冰点", "发酵", "启动", "退潮", "高潮")

    def __init__(self, belief: Mapping[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        if not belief:
            # 未推演：仍渲染五档结构，概率显示占位，推演后填充
            bar = QFrame()
            bar.setObjectName("protoBeliefBarEmpty")
            bar.setStyleSheet(
                f"QFrame#protoBeliefBarEmpty {{ background: {_PANEL_SOFT}; border: none; "
                "border-radius: 3px; }"
            )
            bar.setFixedHeight(12)
            root.addWidget(bar)
            legend = QGridLayout()
            legend.setContentsMargins(0, 0, 0, 0)
            legend.setHorizontalSpacing(12)
            legend.setVerticalSpacing(4)
            for index, name in enumerate(self._ORDER_HINT):
                cell = QVBoxLayout()
                cell.setSpacing(0)
                name_label = QLabel(name)
                name_label.setStyleSheet(
                    f"background: transparent; border: none; color: {_TEXT_3}; font-size: 13px;"
                )
                value_label = QLabel("—")
                value_label.setStyleSheet(
                    f"background: transparent; border: none; color: {_TEXT_3}; "
                    f"font-family: {_FONT_NUM}; font-size: 14px;"
                )
                cell.addWidget(name_label)
                cell.addWidget(value_label)
                legend.addLayout(cell, index // 3, index % 3)
            root.addLayout(legend)
            return
        pairs: list[tuple[str, float]] = []
        for key in self._ORDER_HINT:
            if key in belief:
                pairs.append((key, float(belief[key])))
        for key, value in belief.items():
            if key not in self._ORDER_HINT:
                pairs.append((key, float(value)))
        if not pairs:
            pairs = [(str(k), float(v)) for k, v in belief.items()]
        total = sum(prob for _, prob in pairs) or 1.0
        bar = QFrame()
        bar.setObjectName("protoBeliefBar")
        bar.setStyleSheet(
            f"QFrame#protoBeliefBar {{ background: {_PANEL_SOFT}; border: none; "
            "border-radius: 3px; }"
        )
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(0)
        bar.setFixedHeight(12)
        best = max(pairs, key=lambda item: item[1])
        for name, prob in pairs:
            segment = QFrame()
            segment.setStyleSheet(
                f"background: {_ACCENT if name == best[0] else _SEG_IDLE}; border: none;"
            )
            bar_layout.addWidget(segment, max(1, round(prob / total * 1000)))
        root.addWidget(bar)
        legend = QGridLayout()
        legend.setContentsMargins(0, 0, 0, 0)
        legend.setHorizontalSpacing(12)
        legend.setVerticalSpacing(4)
        for index, (name, prob) in enumerate(pairs):
            cell = QVBoxLayout()
            cell.setSpacing(0)
            name_label = QLabel(name)
            name_label.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT_3}; font-size: 13px;"
            )
            value_label = QLabel(_pct(prob))
            value_label.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT_2}; "
                f"font-family: {_FONT_NUM}; font-size: 14px;"
            )
            cell.addWidget(name_label)
            cell.addWidget(value_label)
            legend.addLayout(cell, index // 3, index % 3)
        root.addLayout(legend)


class _RangeBar(QWidget):
    """Nash equilibrium band ruler (prototype .range)."""

    def __init__(
        self,
        lower: object,
        center: object,
        upper: object,
        position_text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(4)
        ruler = QWidget()
        ruler.setMinimumHeight(30)
        ruler_box = QVBoxLayout(ruler)
        ruler_box.setContentsMargins(0, 6, 0, 6)
        ruler_box.setSpacing(0)
        line = QFrame()
        line.setObjectName("protoRangeLine")
        line.setStyleSheet(
            f"QFrame#protoRangeLine {{ background: {_ACCENT_SOFT}; border: 1px solid #3F5A7E; "
            "border-radius: 2px; }"
        )
        line.setFixedHeight(6)
        ruler_box.addWidget(line)
        marker = QFrame()
        marker.setObjectName("protoRangeMarker")
        marker.setStyleSheet(f"QFrame#protoRangeMarker {{ background: {_ACCENT}; border: none; }}")
        marker.setFixedSize(2, 18)
        ruler_box.addWidget(marker, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(ruler)
        labels = QHBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(0)
        for text, align in (
            (_num(lower, 3), Qt.AlignmentFlag.AlignLeft),
            (_num(center, 3), Qt.AlignmentFlag.AlignHCenter),
            (_num(upper, 3), Qt.AlignmentFlag.AlignRight),
        ):
            label = QLabel(text)
            label.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT_3}; "
                f"font-family: {_FONT_NUM}; font-size: 12px;"
            )
            if align == Qt.AlignmentFlag.AlignHCenter:
                labels.addWidget(label, 1, Qt.AlignmentFlag.AlignHCenter)
            else:
                labels.addWidget(label, 1, align)
        root.addLayout(labels)
        if position_text:
            pos = QLabel(position_text)
            pos.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT_2}; font-size: 14px;"
            )
            root.addWidget(pos)


class _SignalMatrix(QWidget):
    """Two-column signal rows (prototype .signal-table)."""

    def __init__(self, rows: list[tuple[str, object]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for index, (name, value) in enumerate(rows):
            row = QFrame()
            row.setObjectName("protoSignalRow")
            row.setStyleSheet(
                f"QFrame#protoSignalRow {{ background: {_PANEL_RAISED}; border: none; "
                "border-radius: 3px; }"
            )
            box = QHBoxLayout(row)
            box.setContentsMargins(9, 7, 9, 7)
            name_label = QLabel(str(name))
            name_label.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT_3}; font-size: 14px; font-weight: 500;"
            )
            box.addWidget(name_label)
            box.addStretch(1)
            value_label = QLabel(self._value_text(value))
            value_label.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT}; font-size: 14px;"
            )
            box.addWidget(value_label)
            grid.addWidget(row, index // 2, index % 2)

    @staticmethod
    def _value_text(value: object) -> str:
        text = str(value or "—")
        if text == "是":
            return "已触发"
        if text == "否":
            return "未触发"
        return text


class _BehaviorBars(QWidget):
    """Behavior probability bars grouped by participant (prototype .behavior-row)."""

    def __init__(
        self,
        participants: Mapping[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        names = list(participants)
        for index, name in enumerate(names):
            behaviors = participants[name]
            if not isinstance(behaviors, Mapping):
                continue
            column = QWidget()
            column_box = QVBoxLayout(column)
            column_box.setContentsMargins(0, 0, 0, 0)
            column_box.setSpacing(7)
            title = QLabel(str(name))
            title.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT}; font-size: 15px; font-weight: 600;"
            )
            column_box.addWidget(title)
            items: list[tuple[str, float | None]] = []
            for behavior_key, value in behaviors.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    items.append((str(behavior_key), None))
                else:
                    items.append((str(behavior_key), float(value)))
            if items:
                best = max(items, key=lambda item: item[1] if item[1] is not None else -1.0)
                for behavior, prob in items:
                    column_box.addWidget(
                        self._behavior_row(behavior, prob, primary=behavior == best[0])
                    )
            if index > 0:
                divider = QFrame()
                divider.setStyleSheet(f"background: {_LINE}; border: none;")
                divider.setFixedWidth(1)
                root.addWidget(divider)
            root.addWidget(column, 1)

    @staticmethod
    def _behavior_row(name: str, prob: float | None, *, primary: bool) -> QWidget:
        row = QWidget()
        box = QGridLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.setHorizontalSpacing(8)
        box.setVerticalSpacing(2)
        name_label = QLabel(name)
        name_label.setStyleSheet(
            f"background: transparent; border: none; "
            f"color: {_TEXT if primary else _TEXT_2}; font-size: 14px; font-weight: {600 if primary else 400};"
        )
        box.addWidget(name_label, 0, 0)
        track = QFrame()
        track.setStyleSheet(f"background: {_LINE}; border: none;")
        track.setFixedHeight(3 if primary else 2)
        fill = QFrame()
        fill.setStyleSheet(f"background: {_ACCENT if primary else '#62758E'}; border: none;")
        track_box = QHBoxLayout(track)
        track_box.setContentsMargins(0, 0, 0, 0)
        track_box.setSpacing(0)
        if prob is None:
            track_box.addStretch(1000)
        else:
            track_box.addWidget(fill, max(1, round(prob * 1000)))
            track_box.addStretch(max(0, 1000 - round(prob * 1000)))
        box.addWidget(track, 0, 1)
        value_label = QLabel(_pct(prob))
        value_label.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT if prob is not None else _TEXT_3}; "
            f"font-family: {_FONT_NUM}; font-size: 14px;"
        )
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        box.addWidget(value_label, 0, 2)
        box.setColumnStretch(1, 1)
        return row


class _InsightList(QWidget):
    """Bullet list with accent dashes (prototype .insight-list)."""

    def __init__(self, items: list[object], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        for item in items:
            text = str(item)
            row = QWidget()
            box = QHBoxLayout(row)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(8)
            dash = QFrame()
            dash.setStyleSheet(f"background: {_ACCENT}; border: none;")
            dash.setFixedSize(5, 1)
            box.addWidget(dash, 0, Qt.AlignmentFlag.AlignTop)
            label = QLabel(text)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT_2}; font-size: 14px;"
            )
            box.addWidget(label, 1)
            root.addWidget(row)


class _ScenarioCards(QWidget):
    """Primary + alternative scenario cards (prototype .scenario-main)."""

    def __init__(self, branches: list[Mapping[str, Any]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)
        if not branches:
            placeholder = QLabel("暂无情景分支")
            placeholder.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT_3}; font-size: 13px;"
            )
            root.addWidget(placeholder)
            return
        main, alternatives = self._split_main(branches)
        root.addWidget(self._main_card(main))
        if alternatives:
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(9)
            grid.setVerticalSpacing(9)
            for index, branch in enumerate(alternatives):
                grid.addWidget(self._alt_card(branch), index // 2, index % 2)
            root.addLayout(grid)

    @staticmethod
    def _opening_probability(branch: Mapping[str, Any]) -> str:
        return str(branch.get("该情景明天开盘概率") or branch.get("概率") or "—")

    @staticmethod
    def _split_main(branches: list[Mapping[str, Any]]) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
        def prob_of(branch: Mapping[str, Any]) -> float:
            import re

            match = re.search(r"-?\d+(?:\.\d+)?", _ScenarioCards._opening_probability(branch))
            return float(match.group(0)) if match else -1.0

        ordered = sorted(branches, key=prob_of, reverse=True)
        return ordered[0], ordered[1:]

    def _main_card(self, branch: Mapping[str, Any]) -> QFrame:
        card = QFrame()
        card.setObjectName("protoScenarioMain")
        card.setStyleSheet(
            f"QFrame#protoScenarioMain {{ background: #171D26; border: 1px solid #3F5A7E; "
            "border-radius: 4px; }"
        )
        root = QHBoxLayout(card)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        prob_host = QFrame()
        prob_host.setObjectName("protoScenarioProb")
        prob_host.setStyleSheet(
            f"QFrame#protoScenarioProb {{ background: {_ACCENT_SOFT}; border: none; "
            "border-top-left-radius: 3px; border-bottom-left-radius: 3px; }"
        )
        prob_host.setFixedWidth(118)
        prob_box = QVBoxLayout(prob_host)
        prob_box.setContentsMargins(10, 12, 10, 12)
        prob_box.setSpacing(5)
        big = QLabel(self._opening_probability(branch))
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        big.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT}; "
            f"font-family: {_FONT_NUM}; font-size: 27px; font-weight: 650;"
        )
        caption = QLabel("符合预期概率")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT_2}; font-size: 13px;"
        )
        prob_box.addWidget(big)
        prob_box.addWidget(caption)
        root.addWidget(prob_host)
        body = QWidget()
        body_box = QVBoxLayout(body)
        body_box.setContentsMargins(13, 11, 13, 11)
        body_box.setSpacing(5)
        title = QLabel(str(branch.get("情景") or "主情景"))
        title.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT}; font-size: 16px; font-weight: 600;"
        )
        body_box.addWidget(title)
        action = QLabel(str(branch.get("应对") or "暂无可执行动作"))
        action.setWordWrap(True)
        action.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        action.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT_2}; font-size: 14px;"
        )
        body_box.addWidget(action)
        meta = QLabel(
            f"状态：{branch.get('状态') or '—'}　·　开盘首触止损：{branch.get('开盘首次下跌达止损概率') or '—'}"
        )
        meta.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT_3}; font-size: 13px;"
        )
        body_box.addWidget(meta)
        root.addWidget(body, 1)
        return card

    def _alt_card(self, branch: Mapping[str, Any]) -> QFrame:
        card = QFrame()
        card.setObjectName("protoScenarioAlt")
        card.setStyleSheet(
            f"QFrame#protoScenarioAlt {{ background: {_PANEL}; border: 1px solid {_LINE}; "
            "border-radius: 4px; }"
        )
        root = QVBoxLayout(card)
        root.setContentsMargins(12, 10, 12, 11)
        root.setSpacing(5)
        prob = QLabel(self._opening_probability(branch))
        prob.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT}; "
            f"font-family: {_FONT_NUM}; font-size: 23px; font-weight: 650;"
        )
        root.addWidget(prob)
        title = QLabel(str(branch.get("情景") or "偏离情景"))
        title.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT}; font-size: 15px; font-weight: 600;"
        )
        root.addWidget(title)
        action = QLabel(str(branch.get("应对") or "暂无可执行动作"))
        action.setWordWrap(True)
        action.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        action.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT_2}; font-size: 14px;"
        )
        root.addWidget(action)
        meta = QLabel(
            f"状态：{branch.get('状态') or '—'}　·　首触止损：{branch.get('开盘首次下跌达止损概率') or '—'}"
        )
        meta.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT_3}; font-size: 13px;"
        )
        root.addWidget(meta)
        return card


class _FormulaBlock(QFrame):
    """Collapsed-by-default audit block for the sentiment-index formula."""

    def __init__(self, formula: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("protoFormula")
        self.setStyleSheet(
            f"QFrame#protoFormula {{ background: {_PANEL_RAISED}; border: 1px solid {_LINE}; "
            "border-radius: 4px; }"
        )
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(8)
        self.toggle = QToolButton()
        self.toggle.setObjectName("formulaToggle")
        self.toggle.setText("情绪指数计算公式")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setStyleSheet(
            f"QToolButton#formulaToggle {{ background: transparent; border: none; "
            f"color: {_TEXT_2}; font-size: 15px; font-weight: 600; padding: 2px 0; }} "
            f"QToolButton#formulaToggle:hover {{ color: {_TEXT}; }}"
        )
        box.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.content = QLabel(_clean_markdown_text(formula) or "暂无公式")
        self.content.setWordWrap(True)
        self.content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.content.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT_2}; "
            f"font-family: {_FONT_NUM}; font-size: 14px; line-height: 1.5; padding-top: 4px;"
        )
        self.content.hide()
        box.addWidget(self.content)
        self.toggle.toggled.connect(self._set_expanded)

    def _set_expanded(self, expanded: bool) -> None:
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.content.setVisible(expanded)


class _NewsList(QWidget):
    """News items rendered as compact rows with score chips."""

    def __init__(self, news: Mapping[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        items = news.get("items") if isinstance(news, Mapping) else None
        if not isinstance(items, list | tuple) or not items:
            placeholder = QLabel("暂无新闻与事件材料")
            placeholder.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT_3}; font-size: 13px;"
            )
            root.addWidget(placeholder)
            return
        for item in items:
            if not isinstance(item, Mapping):
                continue
            row = QFrame()
            row.setObjectName("protoNewsRow")
            row.setStyleSheet(
                f"QFrame#protoNewsRow {{ background: {_PANEL_RAISED}; border: none; "
                "border-radius: 3px; }"
            )
            box = QVBoxLayout(row)
            box.setContentsMargins(9, 7, 9, 8)
            box.setSpacing(3)
            head = QHBoxLayout()
            head.setSpacing(8)
            title = QLabel(str(item.get("title") or "（无标题）"))
            title.setWordWrap(True)
            title.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT}; font-size: 13px; font-weight: 600;"
            )
            head.addWidget(title, 1)
            score = item.get("sentiment_score")
            score_text = "—" if isinstance(score, bool) or not isinstance(score, (int, float)) else f"{float(score):+.3f}"
            chip = QLabel(score_text)
            chip.setObjectName("protoNewsScore")
            chip.setStyleSheet(
                f"QLabel#protoNewsScore {{ background: {_PANEL_SOFT}; border: 1px solid {_LINE}; "
                "border-radius: 3px; padding: 1px 6px; color: "
                + (_UP if isinstance(score, (int, float)) and not isinstance(score, bool) and score >= 0 else _DOWN if isinstance(score, (int, float)) and not isinstance(score, bool) else _TEXT_3)
                + f"; font-family: {_FONT_NUM}; font-size: 12px; }}"
            )
            head.addWidget(chip)
            box.addLayout(head)
            snippet = str(item.get("snippet") or item.get("summary") or "")
            if snippet:
                body = QLabel(snippet)
                body.setWordWrap(True)
                body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                body.setStyleSheet(
                    f"background: transparent; border: none; color: {_TEXT_2}; font-size: 12px;"
                )
                box.addWidget(body)
            meta_bits = []
            source = item.get("source")
            if source:
                meta_bits.append(str(source))
            published = item.get("published_date") or item.get("published_at") or ""
            if published:
                meta_bits.append(str(published))
            relevance = item.get("relevance")
            if relevance not in (None, ""):
                meta_bits.append(f"相关性 {relevance}")
            if meta_bits:
                meta = QLabel("　·　".join(meta_bits))
                meta.setStyleSheet(
                    f"background: transparent; border: none; color: {_TEXT_3}; font-size: 11px;"
                )
                box.addWidget(meta)
            root.addWidget(row)


class _TextCard(QWidget):
    """Title + selectable text body (used by the market page sections)."""

    def __init__(self, title: str, content: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        if title:
            head = QLabel(title)
            head.setObjectName("secondOrderFieldName")
            head.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT}; font-size: 14px; font-weight: 600;"
            )
            root.addWidget(head)
        body = QLabel(str(content or "—"))
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT_2}; font-size: 14px; line-height: 1.55;"
        )
        root.addWidget(body)


def _styled_text(text: object, *, muted: bool = False) -> QLabel:
    label = QLabel(_clean_markdown_text(text) or "暂无数据")
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setStyleSheet(
        f"background: transparent; border: none; color: {_TEXT_3 if muted else _TEXT_2}; "
        "font-size: 14px; line-height: 1.55;"
    )
    return label


def _data_table(headers: list[object], rows: list[list[object]]) -> QTableWidget:
    table = QTableWidget(len(rows), len(headers))
    table.setObjectName("protoDataTable")
    table.setHorizontalHeaderLabels([_clean_markdown_text(item) for item in headers])
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setAlternatingRowColors(True)
    table.setWordWrap(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.setStyleSheet(
        f"QTableWidget#protoDataTable {{ background: {_PANEL}; border: 1px solid {_LINE}; "
        f"gridline-color: {_LINE}; color: {_TEXT_2}; font-size: 14px; }} "
        f"QHeaderView::section {{ background: {_PANEL_RAISED}; color: {_TEXT_3}; "
        "border: none; padding: 7px; font-size: 13px; font-weight: 600; }} "
        f"QTableWidget::item {{ padding: 5px; }}"
    )
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(list(row)[: len(headers)]):
            item = QTableWidgetItem(_clean_markdown_text(value))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row_index, column_index, item)
    table.resizeRowsToContents()
    height = table.horizontalHeader().height() + sum(
        table.rowHeight(index) for index in range(table.rowCount())
    ) + 6
    table.setFixedHeight(min(max(height, 88), 320))
    return table


class _RankList(QWidget):
    """Compact ranked list used for sector leaders and laggards."""

    def __init__(self, rows: list[list[object]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(7)
        for index, row in enumerate(rows):
            values = list(row)
            rank = _clean_markdown_text(values[0] if values else index + 1)
            name = _clean_markdown_text(values[1] if len(values) > 1 else "")
            change = _clean_markdown_text(values[2] if len(values) > 2 else "")
            rank_label = QLabel(rank.zfill(2) if rank.isdigit() else rank)
            rank_label.setStyleSheet(
                f"color: {_TEXT_3}; font-family: {_FONT_NUM}; font-size: 13px;"
            )
            name_label = QLabel(name)
            name_label.setWordWrap(True)
            name_label.setStyleSheet(f"color: {_TEXT_2}; font-size: 14px;")
            change_label = QLabel(change)
            change_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            color = _UP if change.startswith("+") else _DOWN if change.startswith("-") else _TEXT
            change_label.setStyleSheet(
                f"color: {color}; font-family: {_FONT_NUM}; font-size: 14px;"
            )
            grid.addWidget(rank_label, index, 0)
            grid.addWidget(name_label, index, 1)
            grid.addWidget(change_label, index, 2)
        grid.setColumnStretch(1, 1)


class _MarkdownCard(_Card):
    """A semantic Markdown section rendered as native Qt facts, lists and tables."""

    def __init__(
        self,
        title: str,
        content: object,
        *,
        include_tables: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(_clean_section_title(title), parent=parent)
        model = _markdown_blocks(content)
        for heading, paragraph in model["paragraphs"]:
            if heading:
                subhead = QLabel(heading)
                subhead.setStyleSheet(
                    f"color: {_TEXT}; font-size: 15px; font-weight: 600; padding-top: 2px;"
                )
                self.body.addWidget(subhead)
            self.body.addWidget(_styled_text(paragraph))
        if model["facts"]:
            self.body.addWidget(_FactGrid(model["facts"], columns=2))
        grouped_lists: dict[str, list[str]] = {}
        for heading, item in model["lists"]:
            grouped_lists.setdefault(heading, []).append(item)
        for heading, items in grouped_lists.items():
            if heading:
                subhead = QLabel(heading)
                subhead.setStyleSheet(
                    f"color: {_TEXT}; font-size: 15px; font-weight: 600; padding-top: 2px;"
                )
                self.body.addWidget(subhead)
            self.body.addWidget(_InsightList(items))
        if include_tables:
            for table in model["tables"]:
                if table["title"]:
                    subhead = QLabel(table["title"])
                    subhead.setStyleSheet(
                        f"color: {_TEXT}; font-size: 15px; font-weight: 600; padding-top: 2px;"
                    )
                    self.body.addWidget(subhead)
                self.body.addWidget(_data_table(table["headers"], table["rows"]))
        if not any(model.values()):
            self.body.addWidget(_styled_text(content, muted=True))


class _MarketSummaryBlock(QWidget):
    def __init__(self, content: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        model = _markdown_blocks(content)
        facts = dict(model["facts"])
        paragraphs = [text for _heading, text in model["paragraphs"]]
        signal = facts.get("盘面信号") or "暂无"
        status = paragraphs[0] if paragraphs else "等待形成市场结论"
        note = facts.get("信号依据") or facts.get("操作建议") or ""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        title = QLabel("市场结论")
        title.setObjectName("secondOrderFieldName")
        title.setStyleSheet(f"color: {_TEXT}; font-size: 16px; font-weight: 600;")
        root.addWidget(title)
        root.addWidget(
            _SummaryBand(signal, label="盘面信号", status_text=status, note=note)
        )


class _TradePlanCard(QFrame):
    def __init__(self, content: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("protoTradePlan")
        self.setStyleSheet(
            f"QFrame#protoTradePlan {{ background: #171D25; border: 1px solid #496889; "
            "border-radius: 4px; }"
        )
        model = _markdown_blocks(content)
        facts = dict(model["facts"])
        root = QVBoxLayout(self)
        root.setContentsMargins(13, 11, 13, 13)
        root.setSpacing(9)
        header = QHBoxLayout()
        title = QLabel("明日交易计划")
        title.setObjectName("secondOrderFieldName")
        title.setStyleSheet(f"color: {_TEXT}; font-size: 16px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch(1)
        allocation = QLabel(facts.pop("仓位区间", "仓位待定"))
        allocation.setStyleSheet(
            f"color: {_ACCENT}; font-family: {_FONT_NUM}; font-size: 18px; font-weight: 600;"
        )
        header.addWidget(allocation)
        root.addLayout(header)
        strategy = facts.pop("策略定性", "")
        if strategy:
            root.addWidget(_styled_text(strategy))
        plan_grid = QGridLayout()
        plan_grid.setContentsMargins(0, 0, 0, 0)
        plan_grid.setHorizontalSpacing(8)
        plan_grid.setVerticalSpacing(8)
        fields = [
            ("关注", facts.pop("关注方向", "暂无明确方向")),
            ("回避", facts.pop("回避方向", "暂无明确方向")),
            ("失效触发", facts.pop("失效条件", "等待盘中确认")),
        ]
        for index, (label, value) in enumerate(fields):
            block = QFrame()
            block.setObjectName("protoPlanBlock")
            block.setStyleSheet(
                f"QFrame#protoPlanBlock {{ background: {_PANEL_RAISED}; border: none; border-radius: 3px; }}"
            )
            box = QVBoxLayout(block)
            box.setContentsMargins(10, 9, 10, 10)
            box.setSpacing(4)
            name = QLabel(label)
            name.setStyleSheet(f"color: {_TEXT}; font-size: 14px; font-weight: 600;")
            box.addWidget(name)
            box.addWidget(_styled_text(value))
            plan_grid.addWidget(block, 0 if index < 2 else 1, index if index < 2 else 0, 1, 1 if index < 2 else 2)
        root.addLayout(plan_grid)


# ---------------------------------------------------------------------------
# Page-level renderer
# ---------------------------------------------------------------------------

class PrototypeAnalysisPanel(_AnalysisResultPanel):
    """_AnalysisResultPanel with prototype-styled rendering for one page.

    ``page`` selects the dedicated layout: cycle / game / tree / sector /
    market.  All public methods of the base class keep working; only the
    rendering internals are replaced.
    """

    _SENTIMENT_STATUS_TEXT = {
        "computed": "已计算",
        "pending": "等待推演",
        "market_data_unavailable": "数据源不可用",
        "non_trading_day": "休市保持",
        "insufficient_data": "数据不足",
    }

    def __init__(
        self,
        page: str,
        initial_text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        self._page = page
        super().__init__(initial_text, parent)

    # -- initial / waiting state ------------------------------------------
    def _render_cards(self, fields: Mapping[str, object]) -> None:
        # 初始态（未推演）也渲染完整页面骨架：所有卡片存在，数据为占位符
        self._render_empty_page()

    def _render_empty_page(self) -> None:
        renderer = {
            "cycle": self._render_cycle_page,
            "game": self._render_game_page,
            "tree": self._render_tree_page,
            "sector": self._render_sector_page,
            "market": self._render_market_page,
        }.get(self._page)
        if renderer is not None:
            renderer(self._empty_payload(self._page))

    @staticmethod
    def _empty_payload(page: str) -> object:
        """Structure-complete payload with empty values for the initial render."""
        if page == "cycle":
            return [
                [
                    ("情绪指数", None, 1),
                    ("情绪指数计算公式", "等待二阶推演完成后计算", 2),
                ],
                [("情绪指数明细", {"status": "pending"}, 2)],
                [("LLM 周期观测", "等待大模型给出周期观测", 2)],
                [("HMM 后验信念", {}, 2)],
            ]
        if page == "game":
            return {
                "程序化博弈信号": {
                    "纳什均衡带": {
                        "中心": None,
                        "上沿": None,
                        "下沿": None,
                        "价格位置": "",
                    },
                    "羊群行为": {
                        "羊群买入": None,
                        "羊群卖出": None,
                        "RSI": None,
                        "异常放量": None,
                    },
                    "聪明钱指数": {"净流入为正": None},
                    "机构资金": {"吸筹": None, "派发": None},
                    "流动性陷阱": {"上方陷阱": None, "下方陷阱": None},
                    "反向/动量/回归信号": {
                        "逆势买入": None,
                        "逆势卖出": None,
                        "动量买入": None,
                        "动量卖出": None,
                        "回归买入": None,
                        "回归卖出": None,
                    },
                },
                "参与者识别": {
                    "participant": None,
                    "key_evidence": "等待推演识别参与者与关键证据",
                },
                "参与者先验": {
                    "主力": {
                        "建仓": None,
                        "震仓": None,
                        "拉升": None,
                        "出货": None,
                        "观望": None,
                        "狩猎止损": None,
                    },
                    "散户": {
                        "FOMO追高": None,
                        "恐慌割肉": None,
                        "观望": None,
                        "理性跟随": None,
                        "底部建仓": None,
                        "高位减仓": None,
                    },
                },
                "主导参与者行为推演": {},
            }
        if page == "tree":
            return {
                "B/C三情景概率": [
                    {
                        "情景": "符合预期",
                        "该情景明天开盘概率": "—",
                        "开盘首次下跌达止损概率": "—",
                        "状态": "等待推演",
                        "应对": "等待推演完成后生成应对策略",
                    },
                    {
                        "情景": "超预期强",
                        "该情景明天开盘概率": "—",
                        "开盘首次下跌达止损概率": "—",
                        "状态": "等待推演",
                        "应对": "等待推演完成后生成应对策略",
                    },
                    {
                        "情景": "低于预期",
                        "该情景明天开盘概率": "—",
                        "开盘首次下跌达止损概率": "—",
                        "状态": "等待推演",
                        "应对": "等待推演完成后生成应对策略",
                    },
                ]
            }
        if page == "sector":
            return [
                [
                    (
                        "板块结构",
                        {
                            "sector_name": None,
                            "sector_code": None,
                            "sentiment_index": None,
                            "cycle_position": None,
                        },
                        3,
                    )
                ],
                [
                    ("政策环境", "等待二阶推演识别", 1),
                    ("政策检测", {"状态": "等待检测", "检测环境": None}, 2),
                ],
            ]
        return [
            [("状态", None, 1), ("来源", None, 1)],
            [("DSA 数据日期", None, 1), ("本次决策日期", None, 1)],
            [
                (
                    "模块化大盘分析说明",
                    {"模块化大盘分析": [], "说明": "等待读取 DSA 大盘分析缓存"},
                    4,
                )
            ],
        ]

    @staticmethod
    def _waiting_block(text: str) -> QWidget:
        block = QFrame()
        block.setObjectName("protoWaiting")
        block.setStyleSheet(
            f"QFrame#protoWaiting {{ background: {_PANEL}; border: 1px solid {_LINE}; "
            "border-radius: 4px; }"
        )
        box = QVBoxLayout(block)
        box.setContentsMargins(18, 20, 18, 20)
        label = QLabel(text or "等待分析结果…")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT_2}; font-size: 13px;"
        )
        box.addWidget(label)
        return block

    # -- payload entry points ---------------------------------------------
    def setPlainText(self, text: str) -> None:
        self._raw = text
        self._plain_text = text
        self._render_empty_page()

    def set_payload(self, summary: object, raw: object) -> None:
        self._raw = raw
        self._plain_text = self._format_value(summary)
        if self._page == "game" and isinstance(summary, Mapping):
            self._render_game_page(summary)
        elif self._page == "tree" and isinstance(summary, Mapping):
            self._render_tree_page(summary)
        else:
            super().set_payload(summary, raw)

    def set_grouped_payload(self, rows: list[list[tuple[str, object, int]]], raw: object) -> None:
        self._raw = raw
        self._plain_text = self._format_value(
            {label: value for row in rows for label, value, _stretch in row}
        )
        renderer = {
            "cycle": self._render_cycle_page,
            "sector": self._render_sector_page,
            "market": self._render_market_page,
        }.get(self._page)
        if renderer is not None:
            renderer(rows)
        else:
            super().set_grouped_payload(rows, raw)

    def _append(self, widget: QWidget) -> None:
        self._cards_layout.insertWidget(self._cards_layout.count() - 1, widget)

    def _group(self, rows: list[list[tuple[str, object, int]]], label: str) -> object:
        for row in rows:
            for name, value, _stretch in row:
                if name == label:
                    return value
        return None

    # -- cycle page --------------------------------------------------------
    def _render_cycle_page(self, rows: list[list[tuple[str, object, int]]]) -> None:
        self._clear_cards()
        display = self._group(rows, "情绪指数")
        formula = self._group(rows, "情绪指数计算公式")
        details = self._group(rows, "情绪指数明细")
        observation = self._group(rows, "LLM 周期观测")
        belief = self._group(rows, "HMM 后验信念")

        details_map = details if isinstance(details, Mapping) else {}
        status = str(details_map.get("status") or "")
        status_text = self._SENTIMENT_STATUS_TEXT.get(status, "等待推演" if not status else "已计算")
        note_bits = []
        for key, label in (
            ("news_delta", "消息增量"),
            ("price_action_delta", "行情增量"),
            ("daily_delta", "当日净增量"),
        ):
            value = details_map.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                note_bits.append(f"{label} {value:+.2f}")
        note = "　·　".join(note_bits)
        self._append(_SummaryBand(display, label="情绪指数", status_text=status_text, note=note))
        grid = _TwoColumnGrid()
        observation_map = observation if isinstance(observation, Mapping) else {}
        belief_map = belief if isinstance(belief, Mapping) else {}
        effective_cycle = (
            max(belief_map, key=belief_map.get) if belief_map else "等待 HMM 更新"
        )
        state_card = _Card("状态快照", font_family=_FONT_HEITI)
        state_card.body.addWidget(
            _FactGrid(
                [
                    ("观测周期", observation_map.get("cycle_position") or "等待观测"),
                    ("有效周期", effective_cycle),
                    ("周期事件", observation_map.get("cycle_event") or "暂无"),
                    ("置信度", observation_map.get("confidence") or "暂无"),
                    ("共识状态", observation_map.get("consensus_state") or "暂无"),
                    ("共识方向", observation_map.get("consensus_direction") or "未确认"),
                ],
                columns=2,
                font_family=_FONT_HEITI,
            )
        )
        grid.add(state_card)
        belief_card = _Card("HMM 后验信念")
        belief_card.body.addWidget(_BeliefBar(belief_map))
        grid.add(belief_card)
        evidence = observation_map.get("key_evidence")
        if isinstance(evidence, (list, tuple)) and evidence:
            evidence_card = _Card("判断依据")
            evidence_card.body.addWidget(_InsightList(list(evidence)))
            transition = observation_map.get("transition_reason")
            if transition:
                evidence_card.body.addWidget(_styled_text(transition))
            grid.add(evidence_card, span=2)
        self._append(grid)
        self._append(_FormulaBlock(formula or "等待二阶推演完成后计算"))

    # -- game page ---------------------------------------------------------
    def _render_game_page(self, summary: Mapping[str, Any]) -> None:
        self._clear_cards()
        signals = summary.get("程序化博弈信号")
        participant_analysis = summary.get("参与者识别")
        priors = summary.get("参与者先验")
        forecast = summary.get("主导参与者行为推演")
        grid = _TwoColumnGrid()
        if isinstance(signals, Mapping):
            nash = signals.get("纳什均衡带")
            nash_card = _Card("纳什均衡带")
            if isinstance(nash, Mapping):
                position = {
                    "below": "价格位于均衡带下方",
                    "above": "价格位于均衡带上方",
                    "inside": "价格位于均衡带内",
                    "带下方": "价格位于均衡带下方",
                    "带上方": "价格位于均衡带上方",
                    "带内": "价格位于均衡带内",
                }.get(str(nash.get("价格位置") or ""), "")
                nash_card.body.addWidget(
                    _RangeBar(
                        nash.get("下沿"),
                        nash.get("中心"),
                        nash.get("上沿"),
                        position_text=position,
                    )
                )
            else:
                nash_card.body.addWidget(_styled_text("等待均衡带计算", muted=True))
            grid.add(nash_card)

            momentum_card = _Card("动量与资金")
            herd = signals.get("羊群行为")
            herd = herd if isinstance(herd, Mapping) else {}
            smart = signals.get("聪明钱指数")
            smart = smart if isinstance(smart, Mapping) else {}
            institution = signals.get("机构资金")
            institution = institution if isinstance(institution, Mapping) else {}
            momentum_card.body.addWidget(
                _FactGrid(
                    [
                        ("RSI", herd.get("RSI") or "暂无"),
                        ("异常放量", herd.get("异常放量") or "未触发"),
                        ("聪明钱净流入", smart.get("净流入为正") or "未确认"),
                        (
                            "机构吸筹 / 派发",
                            f"{institution.get('吸筹') or '未触发'} / {institution.get('派发') or '未触发'}",
                        ),
                    ],
                    columns=2,
                )
            )
            grid.add(momentum_card)

            signal_rows: list[tuple[str, object]] = []
            for group_name, group in signals.items():
                if group_name in {"纳什均衡带", "聪明钱指数", "机构资金"} or not isinstance(group, Mapping):
                    continue
                for key, value in group.items():
                    if group_name == "羊群行为" and key in {"RSI", "异常放量"}:
                        continue
                    signal_rows.append((f"{group_name} · {key}", value))
            signal_card = _Card("信号矩阵")
            signal_card.body.addWidget(
                _SignalMatrix(signal_rows) if signal_rows else _styled_text("暂无已触发信号", muted=True)
            )
            grid.add(signal_card, span=2)

        if isinstance(participant_analysis, Mapping):
            card = _Card("参与者识别")
            participant = participant_analysis.get("participant")
            evidence = participant_analysis.get("key_evidence")
            facts: list[tuple[str, object]] = []
            if participant:
                facts.append(("主导参与者", participant))
            for key, label in (
                ("identified_behavior", "行为候选"),
                ("behavior_candidate", "行为候选"),
            ):
                if participant_analysis.get(key):
                    facts.append((label, participant_analysis[key]))
            if facts:
                card.body.addWidget(_FactGrid(facts, columns=2))
            if isinstance(evidence, list | tuple) and evidence:
                card.body.addWidget(_InsightList(list(evidence)))
            elif isinstance(evidence, str) and evidence.strip():
                card.body.addWidget(_InsightList([evidence]))
            elif not facts:
                card.body.addWidget(_TextCard("", str(participant_analysis)))
            grid.add(card)
            contra = participant_analysis.get("contra_evidence")
            if isinstance(contra, (list, tuple)) and contra:
                contra_card = _Card("反向证据")
                contra_card.body.addWidget(_InsightList(list(contra)))
                grid.add(contra_card)

        if forecast is None or isinstance(forecast, Mapping):
            card = _Card("主导参与者行为推演")
            card.setStyleSheet(
                "QFrame#protoCard { background: #171D26; border: 1px solid #3F5A7E; border-radius: 4px; }"
            )
            flat: list[tuple[str, object]] = []
            forecast_map = forecast if isinstance(forecast, Mapping) else {}
            for participant, item in forecast_map.items():
                if not isinstance(item, Mapping):
                    continue
                behavior = item.get("model_behavior")
                probabilities = item.get("probabilities")
                prior_weight = item.get("prior_weight")
                if behavior:
                    flat.append(("参与者", participant))
                    flat.append(("推演行为", behavior))
                    if isinstance(probabilities, Mapping) and probabilities:
                        best = max(probabilities.items(), key=lambda kv: kv[1] if isinstance(kv[1], (int, float)) else -1)
                        flat.append(("行为概率", _pct(best[1]) if isinstance(best[1], (int, float)) else str(best[1])))
                    if prior_weight is not None:
                        flat.append(("先验权重", prior_weight))
            if flat:
                card.body.addWidget(_FactGrid(flat, columns=2))
            else:
                card.body.addWidget(_TextCard("", "等待推演完成后给出主导参与者行为推演"))
            card.body.addWidget(
                _TextCard(
                    "",
                    "专家先验推演，非统计估计。当前缺少可区分主力意图的高成本信号时，概率仅反映先验结构。",
                )
            )
            grid.add(card, span=2)

        if isinstance(priors, Mapping) and priors:
            card = _Card("HMM 行为先验")
            note = QLabel("政策环境修正后的分布。主力与散户始终并列显示。")
            note.setStyleSheet(f"color: {_TEXT_3}; font-size: 13px;")
            card.body.addWidget(note)
            card.body.addWidget(_BehaviorBars(priors))
            grid.add(card, span=2)
        self._append(grid)

    # -- tree page ---------------------------------------------------------
    def _render_tree_page(self, summary: Mapping[str, Any]) -> None:
        self._clear_cards()
        branches = summary.get("B/C三情景概率")
        if not isinstance(branches, list | tuple) or not branches:
            self._append(self._waiting_block("暂无情景分支"))
            return
        self._append(_ScenarioCards([item for item in branches if isinstance(item, Mapping)]))

    # -- sector page -------------------------------------------------------
    def _render_sector_page(self, rows: list[list[tuple[str, object, int]]]) -> None:
        self._clear_cards()
        structure = self._group(rows, "板块结构")
        policy_env = self._group(rows, "政策环境")
        policy_detection = self._group(rows, "政策检测")
        structure_map = structure if isinstance(structure, Mapping) else {}
        grid = _TwoColumnGrid()
        state_card = _Card("板块状态")
        state_facts = []
        for key, label in (
            ("sector_name", "板块名称"),
            ("sector_code", "板块代码"),
            ("sentiment_index", "情绪指数"),
            ("cycle_position", "周期位置"),
            ("effective_cycle_position", "有效周期"),
            ("consensus", "共识状态"),
            ("consensus_state", "共识状态"),
            ("consensus_direction", "共识方向"),
        ):
            if structure_map.get(key) not in (None, "") and not any(
                existing_label == label for existing_label, _value in state_facts
            ):
                state_facts.append((label, structure_map[key]))
        state_card.body.addWidget(
            _FactGrid(state_facts, columns=2)
            if state_facts
            else _styled_text("等待板块状态数据", muted=True)
        )
        grid.add(state_card, span=2)

        if policy_env not in (None, "") or isinstance(policy_detection, Mapping):
            card = _Card("政策环境", font_family=_FONT_HEITI)
            card.setStyleSheet(
                f"QFrame#protoCard {{ background: {_PANEL_RAISED}; border: 1px solid #3F5A7E; "
                f"border-radius: 4px; font-family: {_FONT_HEITI}; }}"
            )
            facts = [("当前判断", policy_env or "等待二阶推演识别")]
            detection_map = policy_detection if isinstance(policy_detection, Mapping) else {}
            for key, label in (
                ("检测环境", "检测环境"),
                ("状态", "检测状态"),
            ):
                if detection_map.get(key) not in (None, ""):
                    facts.append((label, detection_map[key]))
            card.body.addWidget(
                _FactGrid(facts, columns=2, font_family=_FONT_HEITI)
            )
            evidence = detection_map.get("证据链")
            if isinstance(evidence, list | tuple) and evidence:
                items = [
                    f"{entry.get('渠道') or '渠道'}：{entry.get('摘要') or entry}"
                    for entry in evidence
                    if isinstance(entry, Mapping)
                ]
                if items:
                    card.body.addWidget(_InsightList(items))
            grid.add(card, span=2)

        conclusion_keys = (
            "structure_conclusion",
            "conclusion",
            "summary",
            "key_evidence",
            "analysis",
            "signals",
        )
        conclusions: list[object] = []
        for key in conclusion_keys:
            value = structure_map.get(key)
            if isinstance(value, str) and value.strip():
                conclusions.append(value)
            elif isinstance(value, (list, tuple)):
                conclusions.extend(item for item in value if item not in (None, ""))
        conclusion_card = _Card("结构结论")
        conclusion_card.body.addWidget(
            _InsightList(conclusions)
            if conclusions
            else _styled_text("暂无独立结构结论", muted=True)
        )
        grid.add(conclusion_card, span=2)
        self._sector_grid = grid
        self._append(grid)

    # -- market page -------------------------------------------------------
    def _render_market_page(self, rows: list[list[tuple[str, object, int]]]) -> None:
        self._clear_cards()
        status = self._group(rows, "状态")
        source = self._group(rows, "来源")
        data_date = self._group(rows, "DSA 数据日期")
        decision_date = self._group(rows, "本次决策日期")
        explanation = self._group(rows, "模块化大盘分析说明")

        facts = [
            ("状态", status or "—"),
            ("来源", source or "—"),
            ("DSA 数据日期", data_date or "—"),
            ("本次决策日期", decision_date or "—"),
        ]
        self._append(_FactGrid(facts, columns=2))
        grid = _TwoColumnGrid()
        if isinstance(explanation, Mapping):
            sections = explanation.get("模块化大盘分析")
            reason = explanation.get("说明")
            if isinstance(sections, list | tuple) and sections:
                ordered = sorted(
                    (section for section in sections if isinstance(section, Mapping)),
                    key=lambda section: self._market_section_priority(section.get("title")),
                )
                for section in ordered:
                    if not isinstance(section, Mapping):
                        continue
                    title = _clean_section_title(section.get("title") or "大盘模块")
                    content = section.get("content")
                    for widget, span in self._market_section_widgets(title, content):
                        grid.add(widget, span=span)
            else:
                card = _Card("大盘分析")
                card.body.addWidget(
                    _TextCard("", reason or "等待读取 DSA 大盘分析缓存")
                )
                grid.add(card, span=2)
        self._append(grid)

    @staticmethod
    def _market_section_priority(title: object) -> int:
        text = _clean_section_title(title)
        priorities = (
            ("市场结论", 0),
            ("盘面总览", 10),
            ("指数结构", 20),
            ("板块主线", 30),
            ("明日交易计划", 40),
            ("资金与情绪", 50),
            ("消息催化", 60),
            ("风险提示", 70),
            ("免责声明", 80),
        )
        return next((priority for marker, priority in priorities if marker in text), 35)

    @staticmethod
    def _market_section_widgets(title: str, content: object) -> list[tuple[QWidget, int]]:
        model = _markdown_blocks(content)
        if "市场结论" in title:
            clean = _clean_markdown_text(content)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}\s*大盘复盘", clean):
                return []
            return [(_MarkdownCard("市场结论", content), 2)]
        if "盘面总览" in title:
            widgets: list[tuple[QWidget, int]] = [(_MarketSummaryBlock(content), 2)]
            breadth = _Card("市场广度")
            excluded = {"盘面信号", "信号依据", "操作建议"}
            breadth_facts = [fact for fact in model["facts"] if fact[0] not in excluded]
            if breadth_facts:
                breadth.body.addWidget(_FactGrid(breadth_facts, columns=2))
            for table in model["tables"]:
                breadth.body.addWidget(_data_table(table["headers"], table["rows"]))
            if breadth_facts or model["tables"]:
                widgets.append((breadth, 2))
            return widgets
        if "板块主线" in title:
            widgets = []
            narrative = _Card("板块主线")
            paragraphs = [text for _heading, text in model["paragraphs"]]
            narrative.body.addWidget(
                _InsightList(paragraphs)
                if paragraphs
                else _styled_text("等待板块主线结论", muted=True)
            )
            widgets.append((narrative, 2))
            for table in model["tables"]:
                table_card = _Card(table["title"] or "板块排行")
                if len(table["headers"]) == 3 and table["rows"]:
                    table_card.body.addWidget(_RankList(table["rows"]))
                else:
                    table_card.body.addWidget(_data_table(table["headers"], table["rows"]))
                widgets.append((table_card, 1))
            return widgets
        if "明日交易计划" in title:
            return [(_TradePlanCard(content), 2)]
        if "指数结构" in title:
            return [(_MarkdownCard(title, content), 2)]
        if "风险提示" in title:
            risk_items = [item for _heading, item in model["lists"]]
            risk_card = _Card("风险提示")
            risk_card.body.addWidget(
                _InsightList(risk_items)
                if risk_items
                else _styled_text(content)
            )
            widgets = [(risk_card, 2)]
            disclaimers = [
                text
                for _heading, text in model["paragraphs"]
                if text.startswith("注：") or "不构成投资建议" in text
            ]
            if disclaimers:
                disclaimer = QWidget()
                layout = QVBoxLayout(disclaimer)
                layout.setContentsMargins(2, 0, 2, 0)
                note = _styled_text(" ".join(disclaimers), muted=True)
                note.setObjectName("marketDisclaimer")
                layout.addWidget(note)
                widgets.append((disclaimer, 2))
            return widgets
        if "免责声明" in title:
            disclaimer = QWidget()
            layout = QVBoxLayout(disclaimer)
            layout.setContentsMargins(2, 0, 2, 0)
            note = _styled_text(content, muted=True)
            note.setObjectName("marketDisclaimer")
            layout.addWidget(note)
            return [(disclaimer, 2)]
        span = 1 if any(marker in title for marker in ("资金与情绪", "消息催化")) else 2
        return [(_MarkdownCard(title, content), span)]

    def set_table_sections(self, sections: list[Mapping[str, Any]]) -> None:
        if self._page != "sector" or not hasattr(self, "_sector_grid"):
            super().set_table_sections(sections)
            return
        for section in sections:
            if not isinstance(section, Mapping):
                continue
            title = str(section.get("title") or "")
            span = 2 if title == "资金流向" else 1
            self._sector_grid.add(self._build_table_section(section), span=span)

    # -- table sections (sector page) --------------------------------------
    def _build_table_section(self, section: Mapping[str, Any]) -> QWidget:
        """Upgrade the base table section to the prototype palette."""
        box = QFrame()
        box.setObjectName("protoTableSection")
        box.setStyleSheet(
            f"QFrame#protoTableSection {{ background: {_PANEL_RAISED}; border: 1px solid {_LINE}; "
            "border-radius: 4px; }"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel(str(section.get("title") or "明细"))
        title.setStyleSheet(
            f"background: transparent; border: none; color: {_TEXT}; font-size: 16px; font-weight: 600;"
        )
        header.addWidget(title)
        data_date = str(section.get("date") or "")
        if data_date:
            note = QLabel(f"数据截至 {data_date}")
            note.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT_3}; font-size: 13px;"
            )
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
            normalized_rows = [
                list(row) if isinstance(row, (list, tuple)) else [row] for row in rows
            ]
            layout.addWidget(_data_table(headers, normalized_rows))
        else:
            empty = QLabel(str(section.get("empty_text") or "暂无数据"))
            empty.setStyleSheet(
                f"background: transparent; border: none; color: {_TEXT_3}; font-size: 14px; padding: 2px 0;"
            )
            layout.addWidget(empty)
        return box


__all__ = ["PrototypeAnalysisPanel"]
