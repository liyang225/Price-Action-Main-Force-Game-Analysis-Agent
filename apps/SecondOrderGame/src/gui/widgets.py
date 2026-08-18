"""Small native Qt charts used by the parameter workbench."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from src.gui.preview import HeatmapData
from src.gui import tokens


class HeatmapWidget(QWidget):
    """Dependency-free probability heatmap with labels and numeric values."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._data: HeatmapData | None = None
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, data: HeatmapData | None) -> None:
        self._data = data
        if data is not None:
            self.setMinimumHeight(max(190, len(data.row_labels) * 28 + 56))
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(520, 270)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(tokens.SURFACE_1))
        if self._data is None:
            painter.setPen(QColor(tokens.TEXT_SECONDARY))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "无可用预览")
            painter.end()
            return

        data = self._data
        rows, columns = len(data.row_labels), len(data.column_labels)
        left = 86 if rows <= 5 else 118
        top, right, bottom = 46, 14, 30
        plot = QRectF(left, top, max(1, self.width() - left - right), max(1, self.height() - top - bottom))
        cell_w, cell_h = plot.width() / columns, plot.height() / rows

        painter.setPen(QColor(tokens.TEXT_PRIMARY))
        title_font = QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.drawText(QRectF(12, 10, self.width() - 24, 24), data.title)
        painter.setFont(QFont("Cascadia Mono", 8))

        for column, label in enumerate(data.column_labels):
            painter.setPen(QColor(tokens.TEXT_SECONDARY))
            painter.drawText(
                QRectF(plot.left() + column * cell_w, top - 26, cell_w, 22),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )
        for row, label in enumerate(data.row_labels):
            painter.setPen(QColor(tokens.TEXT_SECONDARY))
            painter.drawText(
                QRectF(6, plot.top() + row * cell_h, left - 12, cell_h),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                label,
            )
            for column, value in enumerate(data.values[row]):
                rect = QRectF(plot.left() + column * cell_w, plot.top() + row * cell_h, cell_w, cell_h)
                painter.fillRect(rect.adjusted(1, 1, -1, -1), _probability_color(value))
                painter.setPen(QColor(tokens.TEXT_PRIMARY) if value >= 0.32 else QColor(tokens.TEXT_SECONDARY))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{value:.0%}")
        painter.end()


class DistributionWidget(QWidget):
    """Two-participant horizontal behavior distribution chart."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._distributions: dict[str, dict[str, float]] = {}
        self.setMinimumHeight(280)

    def set_data(self, distributions: dict[str, dict[str, float]] | None) -> None:
        self._distributions = distributions or {}
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(tokens.SURFACE_1))
        painter.setFont(QFont("Cascadia Mono", 8))
        if not self._distributions:
            painter.setPen(QColor(tokens.TEXT_SECONDARY))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "修正配置后恢复行为预览")
            painter.end()
            return

        label_w, value_w = 62, 42
        total_rows = sum(len(distribution) for distribution in self._distributions.values())
        top, row_h = 10, max(17, (self.height() - 20) / (total_rows + len(self._distributions)))
        y = top
        colors = {"主力": QColor(tokens.ACCENT_STEEL), "散户": QColor(tokens.CHART_EMA)}
        for participant, distribution in self._distributions.items():
            behaviors = tuple(distribution)
            painter.setPen(colors.get(participant, QColor("#9AA5B1")))
            painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.DemiBold))
            painter.drawText(QRectF(8, y, self.width() - 16, row_h), participant)
            y += row_h
            painter.setFont(QFont("Microsoft YaHei UI", 8))
            for behavior in behaviors:
                value = float(distribution[behavior])
                painter.setPen(QColor(tokens.TEXT_SECONDARY))
                painter.drawText(QRectF(8, y, label_w - 8, row_h), Qt.AlignmentFlag.AlignVCenter, behavior)
                bar = QRectF(label_w, y + 5, max(1, self.width() - label_w - value_w - 10), row_h - 10)
                painter.fillRect(bar, QColor(tokens.SURFACE_3))
                filled = QRectF(bar.left(), bar.top(), bar.width() * value, bar.height())
                painter.fillRect(filled, colors.get(participant, QColor("#4A7EBB")))
                painter.setPen(QColor(tokens.TEXT_PRIMARY))
                painter.drawText(
                    QRectF(self.width() - value_w, y, value_w - 8, row_h),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    f"{value:.0%}",
                )
                y += row_h
        painter.end()


def _probability_color(value: float) -> QColor:
    low = QColor(tokens.SURFACE_2)
    high = QColor(tokens.ACCENT_STEEL)
    amount = min(1.0, max(0.0, value))
    return QColor(
        round(low.red() + (high.red() - low.red()) * amount),
        round(low.green() + (high.green() - low.green()) * amount),
        round(low.blue() + (high.blue() - low.blue()) * amount),
    )
