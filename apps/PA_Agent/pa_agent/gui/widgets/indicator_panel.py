"""Synchronized lower indicator plot and moving-average controls."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pa_agent.gui.theme import tokens as T

if TYPE_CHECKING:
    from pa_agent.data.base import KlineBar, KlineFrame
    from pa_agent.gui.chart_widget import ChartWidget


_MA_PERIODS = (5, 10, 20, 30, 60)
_MA_COLORS = {
    5: "#5B8CC9",
    10: "#B8933E",
    20: "#8FA3B8",
    30: "#C07A52",
    60: "#9B7EBD",
}

_INDICATOR_HEADER_HEIGHT_PX = 34
_INDICATOR_PLOT_DEFAULT_HEIGHT_PX = 160
_INDICATOR_PANEL_DEFAULT_HEIGHT_PX = (
    _INDICATOR_HEADER_HEIGHT_PX + _INDICATOR_PLOT_DEFAULT_HEIGHT_PX
)


def _colored_stat_name(name: str, color: str | None = None) -> str:
    """Color the indicator name only; its numeric reading stays neutral."""
    if color is None:
        return name
    return f'<span style="color:{color};">{name}</span>'


def sma_newest_first(values: Sequence[float], period: int) -> tuple[float, ...]:
    """Return an exact simple moving average aligned to newest-first input."""
    period = max(1, int(period))
    chronological = [float(value) for value in reversed(values)]
    result = [math.nan] * len(chronological)
    running = 0.0
    for index, value in enumerate(chronological):
        running += value
        if index >= period:
            running -= chronological[index - period]
        if index >= period - 1:
            result[index] = running / period
    return tuple(reversed(result))


def kdj_newest_first(
    bars: Sequence["KlineBar"],
    period: int = 9,
    k_smoothing: int = 3,
    d_smoothing: int = 3,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    """Calculate K, D and J with the standard Chinese-market smoothing rule."""
    chronological = list(reversed(bars))
    k_values: list[float] = []
    d_values: list[float] = []
    j_values: list[float] = []
    k_value = 50.0
    d_value = 50.0
    period = max(1, int(period))
    k_smoothing = max(1, int(k_smoothing))
    d_smoothing = max(1, int(d_smoothing))
    for index, bar in enumerate(chronological):
        window = chronological[max(0, index - period + 1) : index + 1]
        highest = max(float(item.high) for item in window)
        lowest = min(float(item.low) for item in window)
        rsv = 50.0 if highest == lowest else (float(bar.close) - lowest) / (highest - lowest) * 100
        k_value = ((k_smoothing - 1) * k_value + rsv) / k_smoothing
        d_value = ((d_smoothing - 1) * d_value + k_value) / d_smoothing
        k_values.append(k_value)
        d_values.append(d_value)
        j_values.append(3 * k_value - 2 * d_value)
    return (
        tuple(reversed(k_values)),
        tuple(reversed(d_values)),
        tuple(reversed(j_values)),
    )


def macd_newest_first(
    closes: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    """Calculate DIF, DEA and the conventional doubled MACD histogram."""
    chronological = [float(value) for value in reversed(closes)]

    def ema(values: Sequence[float], period: int) -> list[float]:
        if not values:
            return []
        alpha = 2.0 / (max(1, int(period)) + 1.0)
        output = [float(values[0])]
        for value in values[1:]:
            output.append(alpha * float(value) + (1.0 - alpha) * output[-1])
        return output

    fast_values = ema(chronological, fast)
    slow_values = ema(chronological, slow)
    dif = [fast_value - slow_value for fast_value, slow_value in zip(fast_values, slow_values)]
    dea = ema(dif, signal)
    histogram = [2.0 * (dif_value - dea_value) for dif_value, dea_value in zip(dif, dea)]
    return tuple(reversed(dif)), tuple(reversed(dea)), tuple(reversed(histogram))


def _display_value(value: float, *, divisor: float, suffix: str, precision: int) -> str:
    """Format a raw source value for display without changing calculations."""
    if not math.isfinite(float(value)):
        return "--"
    text = f"{float(value) / divisor:.{precision}f}".rstrip("0").rstrip(".")
    return f"{text or '0'}{suffix}"


def _compact_volume(value: float) -> str:
    """Present raw K-line volume using the established ``万`` display scale."""
    return _display_value(value, divisor=1_000_000, suffix="万", precision=2)


def _compact_turnover(value: float) -> str:
    """Present raw K-line turnover in ``亿`` to three decimal places."""
    return _display_value(value, divisor=100_000_000, suffix="亿", precision=3)


def _decimal(value: float) -> str:
    if not math.isfinite(float(value)):
        return "--"
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


class MovingAverageToolbar(QWidget):
    """Compact controls for the moving-average overlays on the K-line plot."""

    overlay_changed = pyqtSignal(bool, object)
    seq_labels_changed = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("movingAverageToolbar")
        self.setFixedHeight(36)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(52, 4, 12, 4)
        layout.setSpacing(8)
        label = QLabel("均线叠加")
        label.setObjectName("studyToolbarLabel")
        layout.addWidget(label)

        self.ma_button = QToolButton()
        self.ma_button.setObjectName("studyMenuButton")
        self.ma_button.setText("MA")
        self.ma_button.setFont(QFont("Microsoft YaHei UI", 8))
        self.ma_button.setArrowType(Qt.ArrowType.NoArrow)
        self.ma_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.ma_button)
        self._ma_actions = {}
        for period in _MA_PERIODS:
            action = menu.addAction(f"MA{period}")
            action.setCheckable(True)
            action.toggled.connect(self._emit_selection)
            self._ma_actions[period] = action
        self.ma_button.setMenu(menu)
        layout.addWidget(self.ma_button)

        # EMA20 and sequence labels remain enabled by default, but are no
        # longer exposed as competing controls in this compact toolbar.
        text_width = QFontMetrics(self.ma_button.font()).horizontalAdvance("MA")
        button_width = max(text_width + 22, round(self.ma_button.sizeHint().width() * 0.8))
        button_height = max(22, round(self.ma_button.sizeHint().height() * 0.8))
        self.ma_button.setFixedSize(button_width, button_height)
        layout.addStretch(1)

    def selected_periods(self) -> tuple[int, ...]:
        return tuple(period for period, action in self._ma_actions.items() if action.isChecked())

    def _emit_selection(self, _checked: bool = False) -> None:
        periods = self.selected_periods()
        self.ma_button.setText("MA")
        self.overlay_changed.emit(True, periods)


class _SilentTimeAxis(pg.AxisItem):
    """Axis with shared tick positions but deliberately hidden coordinate text."""

    def tickStrings(self, values, scale, spacing):  # noqa: N802
        return ["" for _value in values]


class IndicatorSettingsDialog(QDialog):
    """Parameter editor for KDJ, MACD and volume/amount moving averages."""

    def __init__(self, settings: dict[str, object], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("指标参数设置")
        self.setModal(True)
        self.setMinimumWidth(360)
        root = QVBoxLayout(self)

        oscillator_group = QGroupBox("KDJ")
        oscillator_form = QFormLayout(oscillator_group)
        self.kdj_period = self._spin(1, 120, int(settings["kdj_period"]))
        self.k_smoothing = self._spin(1, 30, int(settings["k_smoothing"]))
        self.d_smoothing = self._spin(1, 30, int(settings["d_smoothing"]))
        oscillator_form.addRow("周期 N", self.kdj_period)
        oscillator_form.addRow("K 平滑", self.k_smoothing)
        oscillator_form.addRow("D 平滑", self.d_smoothing)
        root.addWidget(oscillator_group)

        macd_group = QGroupBox("MACD")
        macd_form = QFormLayout(macd_group)
        self.macd_fast = self._spin(1, 120, int(settings["macd_fast"]))
        self.macd_slow = self._spin(2, 240, int(settings["macd_slow"]))
        self.macd_signal = self._spin(1, 120, int(settings["macd_signal"]))
        macd_form.addRow("快线", self.macd_fast)
        macd_form.addRow("慢线", self.macd_slow)
        macd_form.addRow("信号线", self.macd_signal)
        root.addWidget(macd_group)

        volume_group = QGroupBox("成交量 / 成交额均线周期（可直接修改，留空即删除）")
        volume_layout = QGridLayout(volume_group)
        selected = sorted({int(period) for period in settings["volume_ma_periods"]})
        self.volume_ma_period_edits: list[QLineEdit] = []
        for index in range(len(_MA_PERIODS)):
            edit = QLineEdit()
            edit.setObjectName("volumeMaPeriodInput")
            edit.setPlaceholderText("周期")
            edit.setMaxLength(3)
            if index < len(selected):
                edit.setText(str(selected[index]))
            volume_layout.addWidget(edit, index // 3, index % 3)
            self.volume_ma_period_edits.append(edit)
        root.addWidget(volume_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def values(self) -> dict[str, object]:
        fast = self.macd_fast.value()
        slow = max(fast + 1, self.macd_slow.value())
        return {
            "kdj_period": self.kdj_period.value(),
            "k_smoothing": self.k_smoothing.value(),
            "d_smoothing": self.d_smoothing.value(),
            "macd_fast": fast,
            "macd_slow": slow,
            "macd_signal": self.macd_signal.value(),
            "volume_ma_periods": tuple(
                sorted(
                    {
                        int(text)
                        for edit in self.volume_ma_period_edits
                        if (text := edit.text().strip()).isdigit() and 1 <= int(text) <= 999
                    }
                )
            ),
        }


class IndicatorPanel(QWidget):
    """A fixed-height lower study panel synchronized with the K-line chart."""

    def __init__(self, chart: "ChartWidget", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("indicatorPanel")
        # MainWindow's split hotzone gives this drawing area a 160px default
        # and supports manual main/sub-chart split-ratio adjustment.
        self.setMinimumHeight(_INDICATOR_HEADER_HEIGHT_PX + 96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._chart = chart
        self._frame: KlineFrame | None = None
        self._syncing_x_range = False
        self._chart_dragging = False
        self._hover_details: dict[int, tuple[str, float]] = {}
        self._latest_stats_text = "成交量 -- · MA5 -- · MA10 --"
        self._hover_line: pg.InfiniteLine | None = None
        self._hover_x_index: int | None = None
        self._hover_originates_here = False
        self._data_items: dict[str, pg.GraphicsItem] = {}
        self._rendered_indicator_name = ""
        self._settings: dict[str, object] = {
            "kdj_period": 9,
            "k_smoothing": 3,
            "d_smoothing": 3,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "volume_ma_periods": (5, 10),
        }
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("indicatorHeader")
        header.setFixedHeight(_INDICATOR_HEADER_HEIGHT_PX)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(52, 3, 12, 3)
        header_layout.setSpacing(8)
        self.indicator_selector_card = QFrame()
        self.indicator_selector_card.setObjectName("indicatorSelectorCard")
        selector_layout = QHBoxLayout(self.indicator_selector_card)
        selector_layout.setContentsMargins(0, 0, 3, 0)
        selector_layout.setSpacing(2)

        self.indicator_combo = QComboBox()
        self.indicator_combo.setObjectName("indicatorSelector")
        self.indicator_combo.addItems(["成交量", "KDJ", "成交额", "MACD"])
        self.indicator_combo.setFixedWidth(127)
        self.indicator_combo.currentTextChanged.connect(self._render)
        selector_layout.addWidget(self.indicator_combo)
        self.settings_button = QToolButton()
        self.settings_button.setObjectName("indicatorSettingsButton")
        self.settings_button.setText("⚙")
        self.settings_button.setToolTip("设置当前指标参数")
        self.settings_button.clicked.connect(self._open_settings)
        selector_layout.addWidget(self.settings_button)
        header_layout.addWidget(self.indicator_selector_card)
        self.stats_label = QLabel("成交量 -- · MA5 -- · MA10 --")
        self.stats_label.setObjectName("indicatorStats")
        header_layout.addWidget(self.stats_label)
        header_layout.addStretch(1)
        layout.addWidget(header)

        silent_axis = _SilentTimeAxis(orientation="bottom")
        self.plot = pg.PlotWidget(axisItems={"bottom": silent_axis})
        self.plot.setBackground(T.CHART_BG)
        self.plot.showGrid(x=False, y=True, alpha=0.22)
        self.plot.getPlotItem().getAxis("left").setWidth(64)
        self.plot.getPlotItem().getAxis("bottom").setHeight(18)
        self.plot.getViewBox().setMouseEnabled(x=True, y=False)
        self.plot.getViewBox().enableAutoRange(x=False, y=True)
        chart.getViewBox().sigXRangeChanged.connect(self._sync_from_chart)
        self.plot.getViewBox().sigXRangeChanged.connect(self._sync_to_chart)
        chart.axis_width_changed.connect(self._set_axis_width)
        chart.hover_x_changed.connect(self._show_linked_hover)
        chart.chart_dragging_changed.connect(self._set_chart_dragging)
        self.plot.scene().sigMouseMoved.connect(self._on_scene_mouse_moved)
        layout.addWidget(self.plot, 1)

    def set_frame(self, frame: "KlineFrame | None") -> None:
        self._frame = frame
        self._render()

    def _set_axis_width(self, width: int) -> None:
        self.plot.getPlotItem().getAxis("left").setWidth(max(40, int(width)))

    def _sync_from_chart(self, _view_box, x_range) -> None:
        # Follow the main chart continuously, including while it is dragged.
        self._set_exact_x_range(self.plot.getViewBox(), x_range)

    def _sync_to_chart(self, _view_box, x_range) -> None:
        self._set_exact_x_range(self._chart.getViewBox(), x_range)

    def _set_chart_dragging(self, dragging: bool) -> None:
        self._chart_dragging = dragging
        if dragging:
            self._hide_hover(propagate=False)
            return
        self._set_exact_x_range(
            self.plot.getViewBox(),
            self._chart.getViewBox().viewRange()[0],
        )

    def _set_exact_x_range(self, target, x_range) -> None:
        if self._syncing_x_range:
            return
        self._syncing_x_range = True
        try:
            target.setXRange(float(x_range[0]), float(x_range[1]), padding=0)
        finally:
            self._syncing_x_range = False

    def _open_settings(self) -> None:
        dialog = IndicatorSettingsDialog(self._settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._settings = dialog.values()
        self._render()

    def _render(self, *_args) -> None:
        self._hover_details.clear()
        frame = self._frame
        name = self.indicator_combo.currentText()
        if name != self._rendered_indicator_name:
            self._clear_data_items()
            self._rendered_indicator_name = name
        if frame is None or not frame.bars:
            self._clear_data_items()
            self._set_latest_stats(f"{name} --")
            self._create_hover_items()
            return
        if name == "KDJ":
            self._render_kdj(frame)
        elif name == "成交额":
            self._render_volume_like(frame, amount=True)
        elif name == "MACD":
            self._render_macd(frame)
        else:
            self._render_volume_like(frame, amount=False)
        self.plot.getPlotItem().getAxis("left").setWidth(64)
        self._create_hover_items()
        if self._hover_x_index is not None:
            self._show_hover(
                self._hover_x_index,
                propagate=self._hover_originates_here,
            )

    def _clear_data_items(self) -> None:
        for item in self._data_items.values():
            self.plot.removeItem(item)
        self._data_items.clear()

    def _set_bar_data(
        self,
        name: str,
        x_values: Sequence[float],
        heights: Sequence[float],
        *,
        color: str,
    ) -> None:
        item = self._data_items.get(name)
        if not x_values:
            if item is not None:
                self.plot.removeItem(item)
                del self._data_items[name]
            return
        if item is None:
            item = pg.BarGraphItem(
                x=x_values,
                height=heights,
                width=0.68,
                brush=color,
                pen=None,
            )
            self.plot.addItem(item)
            self._data_items[name] = item
        else:
            item.setOpts(x=x_values, height=heights, width=0.68, brush=color, pen=None)

    def _set_curve_data(
        self,
        name: str,
        x_values: Sequence[float],
        y_values: Sequence[float],
        *,
        color: str,
    ) -> None:
        item = self._data_items.get(name)
        if not x_values:
            if item is not None:
                self.plot.removeItem(item)
                del self._data_items[name]
            return
        if item is None:
            item = pg.PlotDataItem(pen=pg.mkPen(color, width=1.2))
            self.plot.addItem(item)
            self._data_items[name] = item
        item.setData(x=x_values, y=y_values)

    def _render_volume_like(self, frame: "KlineFrame", *, amount: bool) -> None:
        bars = frame.bars
        newest_values = tuple(
            float(getattr(bar, "amount", 0.0) if amount else bar.volume) for bar in bars
        )
        chronological_bars = list(reversed(bars))
        x_values = np.arange(len(bars), dtype=float)
        chronological_values = list(reversed(newest_values))
        up_x: list[float] = []
        up_y: list[float] = []
        down_x: list[float] = []
        down_y: list[float] = []
        for x_value, value, bar in zip(x_values, chronological_values, chronological_bars):
            target_x, target_y = (up_x, up_y) if bar.close >= bar.open else (down_x, down_y)
            target_x.append(float(x_value))
            target_y.append(float(value))
        self._set_bar_data("up_bars", up_x, up_y, color=T.CHART_UP)
        self._set_bar_data("down_bars", down_x, down_y, color=T.CHART_DOWN)

        metric_name = "成交额" if amount else "成交量"
        stats: list[tuple[str, float, str | None]] = [(metric_name, newest_values[0], None)]
        ma_series: dict[int, tuple[float, ...]] = {}
        active_ma_names = {
            f"ma_{int(period)}" for period in self._settings["volume_ma_periods"]
        }
        for item_name in tuple(self._data_items):
            if item_name.startswith("ma_") and item_name not in active_ma_names:
                self.plot.removeItem(self._data_items.pop(item_name))
        for period in self._settings["volume_ma_periods"]:
            series = sma_newest_first(newest_values, int(period))
            ma_series[int(period)] = series
            points = [
                (len(bars) - 1 - index, value)
                for index, value in enumerate(series)
                if math.isfinite(value)
            ]
            if points:
                self._set_curve_data(
                    f"ma_{period}",
                    [point[0] for point in points],
                    [point[1] for point in points],
                    color=_MA_COLORS.get(int(period), T.FG_2),
                )
            else:
                self._set_curve_data(f"ma_{period}", (), (), color=T.FG_2)
            stats.append((f"MA{period}", series[0], _MA_COLORS.get(int(period), T.FG_2)))
        volume_unavailable = not amount and not any(value > 0 for value in newest_values)
        if volume_unavailable:
            self._set_latest_stats("成交量：数据源未提供")
        else:
            formatter = _compact_turnover if amount else _compact_volume
            self._set_latest_stats(
                " · ".join(
                    f"{_colored_stat_name(label, color)} {formatter(value)}"
                    for label, value, color in stats
                )
            )
        for x_index in range(len(bars)):
            bar_index = len(bars) - 1 - x_index
            row: list[tuple[str, float, str | None]] = [
                (metric_name, newest_values[bar_index], None)
            ]
            row.extend(
                (
                    f"MA{period}",
                    values[bar_index],
                    _MA_COLORS.get(int(period), T.FG_2),
                )
                for period, values in ma_series.items()
            )
            text = (
                "成交量：数据源未提供"
                if volume_unavailable
                else " · ".join(
                    f"{_colored_stat_name(label, color)} "
                    f"{(_compact_turnover if amount else _compact_volume)(value)}"
                    for label, value, color in row
                )
            )
            self._hover_details[x_index] = (text, newest_values[bar_index])
        self.plot.getPlotItem().setLabel("left", "AMT" if amount else "VOL")

    def _render_kdj(self, frame: "KlineFrame") -> None:
        k_values, d_values, j_values = kdj_newest_first(
            frame.bars,
            int(self._settings["kdj_period"]),
            int(self._settings["k_smoothing"]),
            int(self._settings["d_smoothing"]),
        )
        self._plot_series(frame, k_values, T.ACCENT, name="k")
        self._plot_series(frame, d_values, T.CHART_LINE, name="d")
        self._plot_series(frame, j_values, T.CHART_LINE_3, name="j")
        self._set_latest_stats(
            f"{_colored_stat_name('K', T.ACCENT)} {_decimal(k_values[0])} · "
            f"{_colored_stat_name('D', T.CHART_LINE)} {_decimal(d_values[0])} · "
            f"{_colored_stat_name('J', T.CHART_LINE_3)} {_decimal(j_values[0])}"
        )
        for x_index in range(len(frame.bars)):
            bar_index = len(frame.bars) - 1 - x_index
            text = (
                f"{_colored_stat_name('K', T.ACCENT)} {_decimal(k_values[bar_index])} · "
                f"{_colored_stat_name('D', T.CHART_LINE)} {_decimal(d_values[bar_index])} · "
                f"{_colored_stat_name('J', T.CHART_LINE_3)} {_decimal(j_values[bar_index])}"
            )
            self._hover_details[x_index] = (text, k_values[bar_index])
        self.plot.getPlotItem().setLabel("left", "KDJ")

    def _render_macd(self, frame: "KlineFrame") -> None:
        dif, dea, histogram = macd_newest_first(
            tuple(float(bar.close) for bar in frame.bars),
            int(self._settings["macd_fast"]),
            int(self._settings["macd_slow"]),
            int(self._settings["macd_signal"]),
        )
        chronological_histogram = list(reversed(histogram))
        positive_x = [index for index, value in enumerate(chronological_histogram) if value >= 0]
        negative_x = [index for index, value in enumerate(chronological_histogram) if value < 0]
        self._set_bar_data(
            "positive_bars",
            positive_x,
            [chronological_histogram[index] for index in positive_x],
            color=T.CHART_UP,
        )
        self._set_bar_data(
            "negative_bars",
            negative_x,
            [chronological_histogram[index] for index in negative_x],
            color=T.CHART_DOWN,
        )
        self._plot_series(frame, dif, T.ACCENT, name="dif")
        self._plot_series(frame, dea, T.CHART_LINE, name="dea")
        self._set_latest_stats(
            f"{_colored_stat_name('DIF', T.ACCENT)} {_decimal(dif[0])} · "
            f"{_colored_stat_name('DEA', T.CHART_LINE)} {_decimal(dea[0])} · "
            f"MACD {_decimal(histogram[0])}"
        )
        for x_index in range(len(frame.bars)):
            bar_index = len(frame.bars) - 1 - x_index
            text = (
                f"{_colored_stat_name('DIF', T.ACCENT)} {_decimal(dif[bar_index])} · "
                f"{_colored_stat_name('DEA', T.CHART_LINE)} {_decimal(dea[bar_index])} · "
                f"MACD {_decimal(histogram[bar_index])}"
            )
            self._hover_details[x_index] = (text, histogram[bar_index])
        self.plot.getPlotItem().setLabel("left", "MACD")

    def _plot_series(
        self,
        frame: "KlineFrame",
        values: Sequence[float],
        color: str,
        *,
        name: str,
    ) -> None:
        points = [
            (len(frame.bars) - 1 - index, float(value))
            for index, value in enumerate(values)
            if math.isfinite(float(value))
        ]
        if not points:
            self._set_curve_data(name, (), (), color=color)
            return
        self._set_curve_data(
            name,
            [point[0] for point in points],
            [point[1] for point in points],
            color=color,
        )

    def _set_latest_stats(self, text: str) -> None:
        self._latest_stats_text = text
        self.stats_label.setText(text)

    def _create_hover_items(self) -> None:
        if self._hover_line is not None:
            return
        self._hover_line = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen(
                color=T.CHART_CROSSHAIR,
                width=1,
                style=Qt.PenStyle.DashLine,
            ),
        )
        self._hover_line.setZValue(100)
        self._hover_line.hide()
        self.plot.addItem(self._hover_line)

    def _on_scene_mouse_moved(self, scene_pos) -> None:
        frame = self._frame
        view_box = self.plot.getViewBox()
        if frame is None or not view_box.sceneBoundingRect().contains(scene_pos):
            self._hide_hover(propagate=True)
            return
        point = view_box.mapSceneToView(scene_pos)
        x_index = int(round(float(point.x())))
        if not 0 <= x_index < len(frame.bars):
            self._hide_hover(propagate=True)
            return
        self._show_hover(x_index, propagate=True)

    def _show_linked_hover(self, x_index: object) -> None:
        if x_index is None:
            self._hide_hover(propagate=False)
            return
        self._show_hover(int(x_index), propagate=False)

    def _show_hover(self, x_index: int, *, propagate: bool) -> None:
        detail = self._hover_details.get(int(x_index))
        if detail is None or self._hover_line is None:
            return
        text, _y_value = detail
        self._hover_x_index = int(x_index)
        self._hover_originates_here = propagate
        self._hover_line.setPos(float(x_index))
        self._hover_line.show()
        self.stats_label.setText(text)
        if propagate:
            self._chart.show_external_vertical_guide(x_index)

    def _hide_hover(self, *, propagate: bool) -> None:
        if self._hover_line is not None:
            self._hover_line.hide()
        self._hover_x_index = None
        self._hover_originates_here = False
        self.stats_label.setText(self._latest_stats_text)
        if propagate:
            self._chart.clear_external_vertical_guide()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hide_hover(propagate=True)
        super().leaveEvent(event)
