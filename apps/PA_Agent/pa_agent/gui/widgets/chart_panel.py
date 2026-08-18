"""ChartPanel — wrapper around ChartWidget with titlebar, legend, and footer."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget


class ChartPanel(QWidget):
    """A composite widget that wraps ``ChartWidget`` with chrome UI.

    Layout (vertical):
    - titlebar (symbol / timeframe / meta / status pill)
    - chart_widget (``ChartWidget``, stretch=1)
    - legend (EMA lines + up/down colour key)
    - footer (usage hints + live price read-out)
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chartPanel")

        # Deferred import breaks the circular dependency via pa_agent.gui.__init__
        from pa_agent.gui.chart_widget import ChartWidget

        # ── Root layout ───────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────────────────
        titlebar = QWidget()
        titlebar.setFixedHeight(40)
        titlebar.setStyleSheet(
            "background-color: #12151A;"
            "border-bottom: 1px solid #22272F;"
        )
        title_layout = QHBoxLayout(titlebar)
        title_layout.setContentsMargins(14, 0, 14, 0)
        title_layout.setSpacing(10)

        self._title = QLabel("品种 · 周期")
        self._title.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #E8ECF1;"
            "border: none; background: transparent;"
        )
        title_layout.addWidget(self._title)

        self._meta = QLabel("")
        self._meta.setStyleSheet(
            "font-size: 12px; color: #9AA5B1;"
            "border: none; background: transparent;"
        )
        title_layout.addWidget(self._meta)

        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_layout.addStretch(1)
        title_layout.addWidget(self._status)

        root.addWidget(titlebar)

        # ── Chart widget ──────────────────────────────────────────────────────
        self._chart = ChartWidget(self)
        self._chart.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self._chart, stretch=1)

        # ── Legend ────────────────────────────────────────────────────────────
        legend = QWidget()
        legend.setFixedHeight(28)
        legend.setStyleSheet(
            "background-color: #12151A;"
            "border-top: 1px solid #22272F;"
        )
        legend_layout = QHBoxLayout(legend)
        legend_layout.setContentsMargins(14, 0, 14, 0)
        legend_layout.setSpacing(16)

        for text, color in [
            ("EMA10（灰蓝线）", "#8FA3B8"),
            ("EMA20（赭黄线）", "#B8933E"),
            ("EMA60（赭橙线）", "#C07A52"),
            ("涨（红色）", "#FF4757"),
            ("跌（绿色）", "#00D084"),
        ]:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"font: 11px monospace; color: {color};"
                "border: none; background: transparent;"
            )
            legend_layout.addWidget(lbl)

        legend_layout.addStretch(1)
        root.addWidget(legend)

        # ── Footer ────────────────────────────────────────────────────────────
        footer = QWidget()
        footer.setFixedHeight(28)
        footer.setStyleSheet(
            "background-color: #12151A;"
            "border-top: 1px solid #22272F;"
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(14, 0, 14, 0)
        footer_layout.setSpacing(10)

        self._footer_hint_text = "滚轮缩放 · 拖拽平移 · 当前为分析快照"
        self._footer_left = QLabel(self._footer_hint_text)
        self._footer_left.setStyleSheet(
            "font-size: 11px; color: #9AA5B1;"
            "border: none; background: transparent;"
        )
        footer_layout.addWidget(self._footer_left)

        footer_layout.addStretch(1)

        self._footer_right = QLabel("Price — · EMA20 —")
        self._footer_right.setStyleSheet(
            "font: 11px monospace; color: #9AA5B1;"
            "border: none; background: transparent;"
        )
        footer_layout.addWidget(self._footer_right)

        root.addWidget(footer)

        # Connect hover signal if ChartWidget exposes it
        if hasattr(self._chart, "bar_hovered"):
            self._chart.bar_hovered.connect(self._on_bar_hovered)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_title(self, symbol: str, timeframe: str) -> None:
        """Update the primary title text, e.g. ``XAUUSD · 15m``."""
        self._title.setText(f"{symbol} · {timeframe}")

    def set_meta(self, text: str) -> None:
        """Update the secondary meta label (e.g. bar count / indicator info)."""
        self._meta.setText(text)

    def set_status(self, status: str, text: str = "") -> None:
        """Set the status pill style.

        Parameters
        ----------
        status:
            One of ``"live"``, ``"snapshot"``, ``"error"``.
        text:
            Optional override text. If empty, defaults are used.
        """
        defaults = {
            "live": "实时刷新中",
            "snapshot": "快照冻结",
            "error": "错误",
        }
        display = text or defaults.get(status, status)

        styles = {
            "live": (
                "color: #00D084;"
                "border: 1px solid rgba(0,208,132,0.35);"
                "background-color: rgba(0,208,132,0.10);"
            ),
            "snapshot": (
                "color: #CDA756;"
                "border: 1px solid rgba(192,145,60,0.35);"
                "background-color: rgba(192,145,60,0.10);"
            ),
            "error": (
                "color: #FF4757;"
                "border: 1px solid rgba(255,71,87,0.35);"
                "background-color: rgba(255,71,87,0.10);"
            ),
        }
        base = (
            "border-radius: 999px;"
            "padding: 2px 10px;"
            "font-size: 12px;"
            "background: transparent;"
        )
        self._status.setText(display)
        self._status.setStyleSheet(base + styles.get(status, styles["error"]))

    def set_footer_price(self, price_text: str) -> None:
        """Update the right-hand footer label."""
        self._footer_right.setText(price_text)

    def _on_bar_hovered(self, summary: str) -> None:
        """Show hovered K-line context in the footer."""
        self._footer_left.setText(summary or self._footer_hint_text)

    def chart_widget(self) -> "ChartWidget":  # type: ignore[name-defined]
        """Return the internal ``ChartWidget`` instance for signal connections."""
        return self._chart
