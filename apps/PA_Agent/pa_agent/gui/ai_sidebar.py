"""Right-hand sidebar: live stream, raw I/O, prompt files debug, and decision."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QWheelEvent
from PyQt6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pa_agent.gui.ai_stream_window import AIStreamPanel
from pa_agent.gui.debug_widget import DebugWidget
from pa_agent.gui.theme import tokens as T
from pa_agent.gui.decision_panel import DecisionPanel
from pa_agent.gui.decision_flow_viz import DecisionFlowVizPanel
from pa_agent.gui.decision_tree_panel import DecisionTreePanel
from pa_agent.gui.future_trend_panel import FutureTrendPanel
from pa_agent.gui.history_panel import HistoryPanel
from pa_agent.gui.prompt_files_panel import PromptFilesPanel

if TYPE_CHECKING:
    from pa_agent.config.settings import Settings


class _HistoryNavigationButton(QPushButton):
    """History button that maps wheel direction to older/newer navigation."""

    wheel_navigation_requested = pyqtSignal(int)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self.wheel_navigation_requested.emit(1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)


class AISidebar(QWidget):
    """Workbench sidebar tabs: 交互 | 决策 | 决策树 | 决策树可视化 | 原始 | 调试."""

    previous_history_requested = pyqtSignal()
    next_history_requested = pyqtSignal()
    history_navigation_requested = pyqtSignal(int)

    def __init__(
        self,
        api_key: str = "",
        settings: Optional["Settings"] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("technicalAnalysisSidebar")
        default_font = self.font()
        default_font.setPointSize(default_font.pointSize() + 1)
        self.setFont(default_font)
        self.setStyleSheet(
            "QWidget#technicalAnalysisSidebar QLabel#toolbarTitle { font-size: 16px; }"
            "QWidget#technicalAnalysisSidebar QLabel#mutedLabel { font-size: 12px; }"
            "QWidget#technicalAnalysisSidebar QLabel#stageHeader { font-size: 13px; }"
            "QWidget#technicalAnalysisSidebar QFrame QLabel { font-size: 14px; }"
            "QWidget#technicalAnalysisSidebar QTextEdit#reasoningPane, "
            "QWidget#technicalAnalysisSidebar QPlainTextEdit#reasoningPane, "
            "QWidget#technicalAnalysisSidebar QTextEdit#answerPane, "
            "QWidget#technicalAnalysisSidebar QPlainTextEdit#answerPane { font-size: 13px; }"
            "QWidget#technicalAnalysisSidebar QTableWidget, "
            "QWidget#technicalAnalysisSidebar QTreeWidget, "
            "QWidget#technicalAnalysisSidebar QHeaderView::section { font-size: 14px; }"
            "QWidget#technicalAnalysisSidebar QListWidget#timelineList { font-size: 12px; }"
        )
        self._tabs = QTabWidget()
        self._tabs.setObjectName("analysisSidebarTabs")

        self.stream = AIStreamPanel()
        self.debug = DebugWidget(api_key=api_key)
        self.prompt_files = PromptFilesPanel()
        self.decision = DecisionPanel()
        self.decision_tree = DecisionTreePanel()
        self.decision_flow_viz = DecisionFlowVizPanel()
        self.future_trend = FutureTrendPanel()
        self.history = HistoryPanel()

        self._tabs.addTab(self.history, "历史记录")
        self._tabs.addTab(self.stream, "交互")
        self._tabs.addTab(self.decision, "决策")
        self._tabs.addTab(self.future_trend, "未来走势预期")
        self._tabs.addTab(self.decision_tree, "决策树")
        self._tabs.addTab(self.decision_flow_viz, "决策树可视化")
        self._tabs.addTab(self.debug, "原始")
        self._tabs.addTab(self.prompt_files, "调试")

        history_navigation = QWidget()
        history_navigation.setToolTip("切换当前品种与周期的历史分析")
        history_navigation.setFixedWidth(72)
        navigation_layout = QHBoxLayout(history_navigation)
        navigation_layout.setContentsMargins(6, 0, 0, 0)
        navigation_layout.setSpacing(4)
        self.previous_history_button = _HistoryNavigationButton("◀")
        self.previous_history_button.setToolTip("上一条（更早）历史记录；滚轮向上浏览")
        self.next_history_button = _HistoryNavigationButton("▶")
        self.next_history_button.setToolTip("下一条（更新）历史记录；滚轮向下浏览")
        for button in (self.previous_history_button, self.next_history_button):
            button.setFixedSize(28, 24)
            button.setStyleSheet(
                "QPushButton {"
                f"color: {T.FG}; background-color: {T.SURFACE_2}; border: 1px solid {T.BORDER_SOFT}; "
                f"border-radius: {T.RADIUS}px; font-size: 14px; font-weight: 400; padding: 0;"
                "}"
                f"QPushButton:hover {{ background-color: {T.SURFACE_3}; border-color: {T.SURFACE_4}; }}"
                f"QPushButton:pressed {{ background-color: {T.SURFACE_1}; }}"
                f"QPushButton:disabled {{ color: {T.FG_3}; background-color: {T.SURFACE_1}; border-color: {T.BORDER_SOFT}; }}"
            )
            navigation_layout.addWidget(button)
        self.previous_history_button.clicked.connect(self.previous_history_requested.emit)
        self.next_history_button.clicked.connect(self.next_history_requested.emit)
        self.previous_history_button.wheel_navigation_requested.connect(
            self.history_navigation_requested.emit
        )
        self.next_history_button.wheel_navigation_requested.connect(
            self.history_navigation_requested.emit
        )
        self._tabs.setCornerWidget(history_navigation, Qt.Corner.TopLeftCorner)

        # Keep the common workflow tabs in the strip.  Low-frequency raw and
        # prompt-debug views remain addressable, but move into a right-side menu.
        self._tabs.tabBar().setTabVisible(self.TAB_RAW, False)
        self._tabs.tabBar().setTabVisible(self.TAB_DEBUG, False)
        self._more_button = QToolButton()
        self._more_button.setObjectName("sidebarMoreTabsButton")
        self._more_button.setText("更多")
        self._more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_menu = QMenu(self._more_button)
        self._raw_action = QAction("原始", self._more_menu)
        self._debug_action = QAction("调试", self._more_menu)
        self._raw_action.setCheckable(True)
        self._debug_action.setCheckable(True)
        self._raw_action.triggered.connect(lambda: self._tabs.setCurrentIndex(self.TAB_RAW))
        self._debug_action.triggered.connect(lambda: self._tabs.setCurrentIndex(self.TAB_DEBUG))
        self._more_menu.addAction(self._raw_action)
        self._more_menu.addAction(self._debug_action)
        self._more_button.setMenu(self._more_menu)
        self._tabs.setCornerWidget(self._more_button, Qt.Corner.TopRightCorner)
        # The corner widget's native layout overlaps the leading tab by a few
        # pixels, so reserve one compact gutter after the history arrows.
        self._tabs.setStyleSheet("QTabWidget::tab-bar { left: 8px; }")
        tab_shadow = QGraphicsDropShadowEffect(self._tabs)
        tab_shadow.setBlurRadius(1)
        tab_shadow.setOffset(0, 1)
        tab_shadow.setColor(QColor(255, 255, 255, 13))
        self._tabs.setGraphicsEffect(tab_shadow)
        self.set_history_navigation_enabled(False, False)

        if settings is not None:
            self.bind_settings(settings)

        self._tabs.currentChanged.connect(self._on_tab_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)

    TAB_HISTORY = 0
    TAB_STREAM = 1
    TAB_DECISION = 2
    TAB_FUTURE_TREND = 3
    TAB_DECISION_TREE = 4
    TAB_DECISION_FLOW = 5
    TAB_RAW = 6
    TAB_DEBUG = 7

    def focus_stream(self) -> None:
        """Switch to the live AI output tab (index 0)."""
        self._tabs.setCurrentIndex(self.TAB_STREAM)

    def focus_decision_flow_viz(self) -> None:
        """Switch to the decision flow visualization tab."""
        self._tabs.setCurrentIndex(self.TAB_DECISION_FLOW)

    def _on_tab_changed(self, index: int) -> None:
        self._raw_action.setChecked(index == self.TAB_RAW)
        self._debug_action.setChecked(index == self.TAB_DEBUG)
        if index == self.TAB_DECISION_FLOW:
            self.decision_flow_viz.schedule_refit_view()

    def focus_decision(self) -> None:
        """Switch to the trading decision tab."""
        self._tabs.setCurrentIndex(self.TAB_DECISION)

    def focus_future_trend(self) -> None:
        """Switch to the future trend tab (未来走势预期)."""
        self._tabs.setCurrentIndex(self.TAB_FUTURE_TREND)

    def focus_raw(self) -> None:
        """Switch to the raw I/O tab (原始)."""
        self._tabs.setCurrentIndex(self.TAB_RAW)

    def set_history_navigation_enabled(self, previous: bool, next_: bool) -> None:
        self.previous_history_button.setEnabled(previous)
        self.next_history_button.setEnabled(next_)

    def bind_settings(self, settings: Optional["Settings"]) -> None:
        self.stream.bind_settings(settings)
        self.decision_flow_viz.bind_settings(settings)
