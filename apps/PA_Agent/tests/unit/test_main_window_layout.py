from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QEvent, QObject, QPoint, Qt
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTabWidget,
    QToolButton,
    QWidget,
)


def test_workbench_uses_45_55_split_and_right_docked_pipeline(qtbot, qapp) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.main_window import MainWindow

    window = MainWindow(AppContext(), embedded=True)
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    qapp.processEvents()

    sizes = window._workbench.sizes()
    chart_ratio = sizes[0] / sum(sizes)
    assert 0.43 <= chart_ratio <= 0.47
    assert window._ai_mode_label.isHidden()
    flow_right = window._flow_bar.mapTo(
        window._central,
        QPoint(window._flow_bar.width() - 1, 0),
    ).x()
    assert flow_right == window._central.contentsRect().right() - 12
    assert window._flow_bar.width() == 280
    assert [step._name.text() for step in window._flow_bar._steps] == [
        "数据",
        "快照",
        "诊断",
        "决策",
        "追问",
    ]
    assert window._tf_combo.width() == 72
    short_breadcrumb_width = window._instrument_control_group.width()
    short_fetch_x = window._fetch_data_btn.mapTo(window._central, QPoint()).x()
    window._set_embedded_breadcrumb_text("超长标的名称" * 24)
    qapp.processEvents()
    assert window._instrument_breadcrumb.text().endswith("…")
    assert window._instrument_control_group.width() > short_breadcrumb_width
    # The action cluster is docked right via the stretch, so it stays put while
    # the breadcrumb grows into the freed space in the same row.
    assert window._fetch_data_btn.mapTo(window._central, QPoint()).x() == short_fetch_x
    assert [window._strategy_browser.tabText(i) for i in range(2)] == [
        "技术分析",
        "二阶博弈",
    ]
    assert window._strategy_stack.count() == 2
    assert window._strategy_stack.currentIndex() == 0
    assert window._second_order_workspace.timeframe() == "120m"
    assert window._second_order_workspace.analysis_tabs() == (
        "概览",
        "情绪周期",
        "博弈推演",
        "应对树",
        "T+1 闸门",
        "板块分析",
        "大盘分析",
        "材料缓存",
        "历史回测",
        "LLM 对话",
        "原始",
        "设置",
    )
    second_order_tabs = window._second_order_workspace._tabs
    raw_index = second_order_tabs.indexOf(window._second_order_workspace._raw_tab)
    settings_index = second_order_tabs.indexOf(window._second_order_workspace._settings)
    assert not second_order_tabs.tabBar().isTabVisible(raw_index)
    assert not second_order_tabs.tabBar().isTabVisible(settings_index)
    assert second_order_tabs.cornerWidget(Qt.Corner.TopRightCorner) is (
        window._second_order_workspace._more_button
    )
    assert [
        action.text()
        for action in window._second_order_workspace._more_button.menu().actions()
    ] == ["原始", "设置"]
    assert window._second_order_workspace._prior_disclaimer.text() == (
        "专家先验推演，非统计估计"
    )
    assert not window._second_order_workspace._prior_disclaimer.isHidden()
    assert "独立对话" in window._second_order_workspace._chat_transcript.toPlainText()
    assert "不读取 PA 技术分析" in window._second_order_workspace._chat_transcript.toPlainText()
    window._strategy_browser.setCurrentIndex(1)
    qapp.processEvents()
    assert window._strategy_stack.currentIndex() == 1
    assert window._strategy_browser.currentIndex() == 1
    second_order = window._second_order_workspace
    chart_top = second_order._chart.mapTo(
        second_order._content_split, QPoint()
    ).y()
    chart_bottom = second_order._chart.mapTo(
        second_order._content_split, QPoint(0, second_order._chart.height())
    ).y()
    tabs_bottom = second_order._tabs.mapTo(
        second_order._content_split, QPoint(0, second_order._tabs.height())
    ).y()
    assert chart_top == second_order._chart_legend.height() + 36
    assert chart_top < 80
    assert tabs_bottom - chart_bottom == 60
    window._strategy_browser.setCurrentIndex(0)
    assert window._wait_close_checkbox.text() == "最新K线收盘后再分析"
    assert window._fetch_data_btn.x() < window._submit_btn.x()
    assert window._fetch_data_btn.objectName() == "fetchDataButton"
    separators = window._central.findChildren(QFrame, "controlBarSeparator")
    assert len(separators) == 2
    assert separators[0].parentWidget().layout().spacing() == 3
    assert all(separator.mapTo(window._central, QPoint()).x() == 0 for separator in separators)
    assert all(separator.width() == window._central.width() for separator in separators)
    strategy_separator = window._central.findChild(QFrame, "strategyRowSeparator")
    assert strategy_separator is not None
    strategy_bottom = window._instrument_control_group.mapTo(
        window._central, QPoint(0, window._instrument_control_group.height())
    ).y()
    separator_top = strategy_separator.mapTo(window._central, QPoint()).y()
    controls_top = window._module_controls.mapTo(window._central, QPoint()).y()
    assert separator_top - strategy_bottom == 3
    assert controls_top - strategy_bottom == 8
    assert strategy_separator.styleSheet() == ""
    assert window._submit_btn.x() < window._wait_close_checkbox.x()
    assert window._wait_close_checkbox.x() < window._wait_close_countdown_label.x()
    assert window._wait_close_countdown_label.geometry().right() < window._keep_analysis_checkbox.x()
    flow_left = window._flow_bar.mapTo(window._central, QPoint()).x()
    # The action cluster (selector row) and the flow bar (tab row) are both
    # docked to the same right edge.
    action_bar_right = window._technical_action_bar.mapTo(
        window._central, QPoint(window._technical_action_bar.width() - 1, 0)
    ).x()
    flow_right = window._flow_bar.mapTo(
        window._central, QPoint(window._flow_bar.width() - 1, 0)
    ).x()
    assert abs(action_bar_right - flow_right) <= 2
    assert flow_left < action_bar_right
    assert not hasattr(window, "_fit_chart_btn")
    assert not hasattr(window, "_chart_refresh_switch")
    assert not hasattr(window, "_resume_chart_btn")
    assert window._keep_analysis_checkbox.text() == "持续分析"

    window.resize(1920, 900)
    full_name = "消费电子ETF (159732.SZ)"
    window._set_embedded_breadcrumb_text(full_name)
    qapp.processEvents()
    assert window._instrument_breadcrumb.text() == full_name

    assert window._ai_sidebar._tabs.tabText(window._ai_sidebar.TAB_STREAM) == "交互"
    assert window._ai_sidebar._tabs.tabText(window._ai_sidebar.TAB_DECISION) == "决策"
    assert window._ai_sidebar._tabs.tabText(window._ai_sidebar.TAB_DECISION_TREE) == "决策树"
    tab_bar = window._ai_sidebar._tabs.tabBar()
    assert tab_bar.tabRect(window._ai_sidebar.TAB_STREAM).x() < tab_bar.tabRect(
        window._ai_sidebar.TAB_DECISION
    ).x() < tab_bar.tabRect(window._ai_sidebar.TAB_DECISION_TREE).x()
    history_tab_x = tab_bar.mapTo(
        window._ai_sidebar,
        tab_bar.tabRect(window._ai_sidebar.TAB_HISTORY).topLeft(),
    ).x()
    assert history_tab_x >= window._ai_sidebar.next_history_button.geometry().right() + 4
    assert history_tab_x <= window._ai_sidebar.next_history_button.geometry().right() + 16
    assert window._ai_sidebar._more_button.isVisible()
    assert not tab_bar.isTabVisible(window._ai_sidebar.TAB_RAW)
    assert not tab_bar.isTabVisible(window._ai_sidebar.TAB_DEBUG)
    assert window._market_summary_strip.height() == 44
    assert window._market_summary_strip.layout().contentsMargins().left() == 52
    assert window._moving_average_toolbar.height() == 36
    assert window._indicator_panel.plot.height() == 160
    assert window._chart_indicator_splitter.handleWidth() == 6
    assert window._chart_indicator_hotzone.objectName() == "splitHotzone"
    assert window._chart_indicator_hotzone.cursor().shape() == Qt.CursorShape.SizeVerCursor
    assert window._workbench.parentWidget().layout().spacing() == 0
    assert window._workbench.parentWidget().parentWidget().layout().contentsMargins().bottom() == 6
    assert window._workbench.parentWidget().parentWidget().layout().spacing() == 1
    assert window._workbench.handleWidth() == 18
    assert window._fetch_data_btn.mapTo(window._central, QPoint()).y() > window._flow_bar.mapTo(
        window._central, QPoint()
    ).y()
    assert window._wait_close_checkbox.x() > window._fetch_data_btn.x()
    assert all(
        label.text() != "分析仅供参考，不构成投资建议"
        for label in window._status_bar.findChildren(QLabel)
    )
    window._wait_close_checkbox.setChecked(True)
    qapp.processEvents()
    assert not window._pending_submit_after_close
    assert not window._submit_btn.isEnabled()


def test_strategy_switch_replaces_the_top_action_controls_and_isolates_pipelines(qtbot, qapp) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.main_window import MainWindow

    window = MainWindow(AppContext(), embedded=True)
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    qapp.processEvents()

    assert window._module_controls_stack.currentIndex() == 0
    assert window._action_controls_stack.currentIndex() == 0
    assert window._pipeline_stack.currentIndex() == 0
    assert window._technical_controls.isVisible()
    assert window._technical_action_bar.isVisible()
    assert not window._second_order_workspace.control_bar().isVisible()
    assert window._flow_bar.isVisible()
    assert not window._second_order_flow_bar.isVisible()

    window._flow_bar.set_step_status(2, "active")
    window._strategy_browser.setCurrentIndex(1)
    qapp.processEvents()

    assert window._module_controls_stack.currentIndex() == 1
    assert window._action_controls_stack.currentIndex() == 1
    assert window._pipeline_stack.currentIndex() == 1
    assert not window._technical_controls.isVisible()
    assert not window._technical_action_bar.isVisible()
    assert window._second_order_workspace.control_bar().isVisible()
    assert not window._flow_bar.isVisible()
    assert window._second_order_flow_bar.isVisible()
    second_order_controls = window._second_order_workspace.control_bar()
    assert second_order_controls.layout().indexOf(
        window._second_order_workspace._symbol
    ) == -1
    assert second_order_controls.layout().indexOf(
        window._second_order_workspace._status
    ) == -1
    assert not window._second_order_workspace._symbol.isVisible()
    assert not window._second_order_workspace._status.isVisible()
    # The second-order action cluster sits in the selector row below the flow
    # bar, both docked to the same right edge.
    run_right = window._second_order_workspace._run.mapTo(
        window._central,
        QPoint(window._second_order_workspace._run.width() - 1, 0),
    ).x()
    run_top = window._second_order_workspace._run.mapTo(
        window._central, QPoint()
    ).y()
    flow_right = window._second_order_flow_bar.mapTo(
        window._central,
        QPoint(window._second_order_flow_bar.width() - 1, 0),
    ).x()
    flow_top = window._second_order_flow_bar.mapTo(
        window._central, QPoint()
    ).y()
    assert abs(run_right - flow_right) <= 2
    assert run_top > flow_top
    assert [step._name.text() for step in window._second_order_flow_bar._steps] == [
        "行情", "材料", "推演", "闸门", "归档",
    ]

    window._second_order_workspace.pipeline_step_changed.emit(1, "active")
    assert window._second_order_flow_bar._steps[1].status == "active"
    assert window._flow_bar._steps[2].status == "active"

    window._strategy_browser.setCurrentIndex(0)
    qapp.processEvents()
    assert window._flow_bar.isVisible()
    assert window._flow_bar._steps[2].status == "active"


def test_second_order_handoff_flattens_current_pa_trade_decision(
    monkeypatch, qtbot
) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui import second_order_workspace as module
    from pa_agent.gui.main_window import MainWindow

    monkeypatch.setattr(
        module,
        "_refresh_dsa_market_cache",
        lambda *_args: {"status": "not_configured"},
    )

    class Record:
        stage1_diagnosis = {"cycle_position": "启动"}
        stage2_decision = {
            "decision": {
                "order_type": "限价单",
                "order_direction": "做多",
                "entry_price": 10.2,
                "take_profit_price": 11.0,
                "stop_loss_price": 9.8,
                "estimated_win_rate": 63,
            }
        }

        def model_dump(self, mode="json"):
            return {
                "stage1_diagnosis": self.stage1_diagnosis,
                "stage2_decision": self.stage2_decision,
            }

    window = MainWindow(AppContext(), embedded=True)
    qtbot.addWidget(window)
    window._last_analysis_record = Record()
    window._symbol_combo.setCurrentText("000001.SZ")

    payload = window._second_order_handoff_payload()

    assert payload["symbol"] == "000001.SZ"
    assert payload["stock_name"]
    assert payload["should_trade"] is True
    assert payload["order_type"] == "限价单"
    assert payload["order_direction"] == "做多"
    assert payload["entry_price"] == 10.2
    assert payload["take_profit_price"] == 11.0
    assert payload["stop_loss_price"] == 9.8
    assert payload["estimated_win_rate"] == 63
    assert payload["decision_point"] in {"midday", "close"}


def test_second_order_handoff_uses_current_stage1_before_record_is_saved(
    monkeypatch, qtbot
) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.main_window import MainWindow
    from pa_agent.gui import second_order_workspace as module

    monkeypatch.setattr(
        module,
        "_refresh_dsa_market_cache",
        lambda *_args: {"status": "not_configured"},
    )
    window = MainWindow(AppContext(), embedded=True)
    qtbot.addWidget(window)
    class StaleRecord:
        stage1_diagnosis = {"trend": "旧趋势"}

    window._last_analysis_record = StaleRecord()
    window._last_stage1_diagnosis = {
        "trend": "上升趋势",
        "kline_structure": "缩量回踩支撑",
    }

    payload = window._second_order_handoff_payload(
        {"decision": {"order_type": "不下单"}},
        stage1_diagnosis=window._last_stage1_diagnosis,
    )

    assert payload["order_type"] == "不下单"
    assert payload["should_trade"] is False
    assert payload["stage1_diagnosis"] == window._last_stage1_diagnosis


def test_second_order_chat_history_is_owned_by_each_workspace(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    left = SecondOrderWorkspace(AppContext())
    right = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(left)
    qtbot.addWidget(right)

    left._run_analysis = lambda: None  # avoid starting a network worker
    left._chat_input.setText("只属于左侧二阶窗口")
    left._send_chat_context()

    assert left._chat_context == [{"role": "user", "content": "只属于左侧二阶窗口"}]
    assert right._chat_context == []


def test_second_order_workspace_exposes_each_production_stage(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    workspace.resize(1200, 800)
    workspace.show()
    qtbot.wait(20)

    assert {
        "概览",
        "情绪周期",
        "博弈推演",
        "应对树",
        "T+1 闸门",
        "板块分析",
        "大盘分析",
        "材料缓存",
        "历史回测",
        "LLM 对话",
        "原始",
        "设置",
    } <= set(workspace.analysis_tabs())
    assert workspace._sector_name_edit.placeholderText()
    assert workspace._sector_code_edit.placeholderText() == (
        "例如：SH.LIST0022、HK.LIST1910 或 US.LIST20077"
    )
    assert "原样提交给富途 OpenD" in workspace._sector_code_edit.toolTip()
    assert "DSA 路径中的 data 文件夹" in workspace._dsa_database_edit.placeholderText()
    assert not hasattr(workspace, "_free_chat_session")
    assert workspace._run_news_prefetch_toggle.text() == "正式推演前强制预取消息"
    assert workspace._run_material_preanalysis_toggle.text() == "正式推演前强制预分析材料"
    assert workspace._run_news_prefetch_toggle.isChecked()
    assert workspace._run_material_preanalysis_toggle.isChecked()
    assert not workspace._run_news_prefetch_toggle.isEnabled()
    assert not workspace._run_material_preanalysis_toggle.isEnabled()
    assert workspace._news_prefetch_toggle.text() == "定时消息预取"
    assert workspace._material_preanalysis_toggle.text() == "定时材料预分析"
    assert workspace._news_prefetch_time.time().toString("HH:mm") == "09:35"
    assert workspace._material_preanalysis_time.time().toString("HH:mm") == "09:40"
    assert workspace._news_prefetch_timer.isSingleShot()
    assert workspace._material_preanalysis_timer.isSingleShot()
    assert workspace._material_runtime_text.isReadOnly()
    assert not hasattr(workspace._overview, "_text")
    assert workspace._overview._cards_layout.count() >= 2
    split_sizes = workspace._content_split.sizes()
    assert 0.37 <= split_sizes[0] / sum(split_sizes) <= 0.43
    assert workspace._tabs.focusPolicy() == Qt.FocusPolicy.NoFocus
    regenerate_index = workspace._market._header_layout.indexOf(
        workspace._market_regenerate
    )
    raw_index = workspace._market._header_layout.indexOf(workspace._market._raw_button)
    button_gap = workspace._market._header_layout.itemAt(regenerate_index + 1).spacerItem()
    assert raw_index == regenerate_index + 2
    assert button_gap is not None
    assert button_gap.sizeHint().width() == 8


def test_second_order_raw_page_keeps_failed_model_exchange(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    workspace._render_llm_trace(
        {
            "ok": False,
            "status": "error",
            "error": "model response is not a standalone JSON object: Expecting value",
            "llm_trace": [
                {
                    "request": "CycleModelOutput",
                    "messages": [
                        {"role": "system", "content": "仅返回 JSON"},
                        {"role": "user", "content": "情绪周期分析材料"},
                    ],
                    "response": {"content": "不是 JSON"},
                    "error": {
                        "code": "invalid_response_format",
                        "message": "model response is not a standalone JSON object",
                    },
                }
            ],
        }
    )

    raw = workspace._raw_text.toPlainText()
    assert "情绪周期分析材料" in raw
    assert "不是 JSON" in raw
    assert "invalid_response_format" in raw
    workspace._more_button.menu().actions()[0].trigger()
    assert workspace._tabs.currentWidget() is workspace._raw_tab


def test_second_order_overview_flow_tracks_all_main_stages(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    flow = workspace._overview_flow

    assert flow.NODE_NAMES == (
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
    assert flow._states == ["pending"] * 11

    workspace._worker_progress("analysis", "settings", "active")
    assert flow.active_index == 1
    assert flow._states[1] == "active"
    workspace._worker_progress("analysis", "settings", "done")
    workspace._worker_progress("analysis", "materials", "active")
    assert flow._states[1:3] == ["done", "active"]


def test_second_order_overview_flow_supports_zoom_and_error_row(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    flow = workspace._overview_flow
    before = flow._view.transform().m11()

    flow._zoom(1.18)
    flow.set_status(6, "error", "模型请求超时")

    assert flow._view.transform().m11() > before
    assert flow._error_label.isVisible() is False
    workspace.show()
    qtbot.wait(10)
    assert flow._error_label.isVisible()
    assert "异常节点：大模型推演" in flow._error_label.text()
    assert "模型请求超时" in flow._error_label.text()


def test_second_order_overview_flow_keeps_nodes_at_120_pixels(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    flow = workspace._overview_flow
    rects = [item.rect() for item in flow._scene.items() if hasattr(item, "rect")]

    assert any(rect.width() == 120 for rect in rects)


def test_second_order_overview_flow_marks_stalled_active_node(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    flow = workspace._overview_flow
    flow.set_status(7, "active")

    flow._mark_active_stalled()

    assert flow._states[7] == "stalled"
    assert "B/C概率计算" in flow._error_label.text()
    assert "超过 30 秒未收到阶段进展或模型输出" in flow._error_label.text()


def test_material_cache_status_loads_when_tab_is_first_opened(
    monkeypatch, qtbot
) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui import second_order_workspace as module

    class Service:
        def __init__(self, **_kwargs) -> None:
            pass

        def material_cache_status(self):
            return {
                "trading_date": "2026-08-14",
                "state": "filling",
                "categories": {"news": 2},
            }

        def material_cache_preview(self):
            return {"news": {"半导体": {}, "消费电子": {}}}

        def material_cache_news(self):
            return {
                "半导体": {
                    "sector_code": "SH.BK0001",
                    "count": 1,
                    "sentiment_sum": 0.5,
                    "items": [
                        {
                            "title": "示例新闻",
                            "url": "https://example.test/1",
                            "published_date": "2026-08-14",
                            "code": ["SH.600519"],
                            "source": "新华社",
                            "sentiment_score": 0.5,
                            "relevance": 1.0,
                            "validity": 0.8,
                            "source_credibility": 0.85,
                            "subject_purpose": "产业扶持",
                        }
                    ],
                }
            }

    monkeypatch.setattr(
        module,
        "_load_second_order_modules",
        lambda: (object(), Service, object()),
    )
    monkeypatch.setattr(
        module,
        "_refresh_dsa_market_cache",
        lambda *_args: {"status": "not_configured"},
    )
    workspace = module.SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)

    workspace._tabs.setCurrentWidget(workspace._materials)
    qtbot.wait(10)

    displayed = json.loads(workspace._material_runtime_text.toPlainText())
    assert displayed["任务"] == "状态刷新"
    assert displayed["缓存生命周期"]["trading_date"] == "2026-08-14"
    assert displayed["缓存类别"] == {"news": 2}
    assert workspace._material_news_table.headerItem().text(6) == "最终加权分"
    sector_row = workspace._material_news_table.topLevelItem(0)
    assert sector_row.text(6) == "+0.500"
    assert sector_row.child(0).text(7) == "1.000"
    assert sector_row.child(0).text(8) == "0.800"
    assert sector_row.child(0).text(9) == "0.850"


def test_second_order_result_cards_and_llm_audit_follow_requested_layout(qtbot, qapp) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    workspace._render_analysis_result(
        {
            "news_details": {
                "半导体": {
                    "sector_code": "SH.BK0001",
                    "sentiment_sum": 0.5,
                    "items": [{"title": "新闻A", "sentiment_score": 0.5}],
                },
                "消费电子": {
                    "sector_code": "SH.BK0002",
                    "sentiment_sum": -0.2,
                    "items": [{"title": "新闻B", "sentiment_score": -0.2}],
                },
            },
            "result": {
                "input": {
                    "symbol": "600519.SH",
                    "decision_point": "close",
                    "policy_environment": "政策暖风",
                    "sector_belief": {"发酵": 0.7},
                    "materials": {
                        "sector_analysis": {"sentiment_index": 68.5},
                        "participant_priors": {
                            "主力": {"建仓": 0.5, "震仓": 0.1, "拉升": 0.2, "出货": 0.05, "观望": 0.1, "狩猎止损": 0.05},
                        },
                        "market_analysis": {
                            "source": "DSA analysis_history",
                            "data_date": "2026-08-14",
                            "decision_date": "2026-08-14",
                            "status": "ready",
                            "display_sections": [{"title": "市场结论", "content": "偏强"}],
                            "reason": "同日数据可用",
                        },
                    },
                },
                "scenario_tree": {"branches": [], "analysis_metadata": {}},
                "integrated_gates": {},
            },
            "llm_trace": [
                {
                    "request": "CycleModelOutput",
                    "prompt_files": [r"C:\prompts\情绪周期判断.txt"],
                    "messages": [
                        {"role": "system", "content": "完整系统提示词"},
                        {"role": "user", "content": "完整用户原文"},
                    ],
                }
            ],
        },
        refresh_history=False,
    )
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    cycle_names = [
        label.text()
        for label in workspace._cycle.findChildren(QLabel, "secondOrderFieldName")
    ]
    market_names = [
        label.text()
        for label in workspace._market.findChildren(QLabel, "secondOrderFieldName")
    ]
    sector_names = [
        label.text()
        for label in workspace._sector.findChildren(QLabel, "secondOrderFieldName")
    ]
    game_names = [
        label.text()
        for label in workspace._game_reasoning.findChildren(QLabel, "secondOrderFieldName")
    ]
    assert cycle_names[0] == "情绪指数"
    for expected in ("状态", "来源", "DSA 数据日期", "本次决策日期", "市场结论"):
        assert expected in market_names, f"market panel missing {expected!r}: {market_names}"
    assert sector_names[0] == "政策环境"
    assert "板块状态" in sector_names
    assert "结构结论" in sector_names
    assert "政策环境" in sector_names
    assert "周期来源" not in sector_names
    assert "HMM 行为先验" not in sector_names
    assert "主导参与者行为推演" in game_names
    assert "HMM 行为先验" in game_names
    assert game_names.index("主导参与者行为推演") < game_names.index("HMM 行为先验")
    formula_toggle = workspace._cycle.findChild(QToolButton, "formulaToggle")
    assert formula_toggle is not None
    assert not formula_toggle.isChecked()
    assert formula_toggle.parent().content.isHidden()
    assert workspace._overview._scroll.isHidden()
    assert workspace._overview._raw_button.text() == "原始数据"
    assert workspace._labeler_status_card.maximumHeight() == 64
    assert workspace._market._cards_layout.count() >= 2  # header fact-grid + section cards + stretch
    assert workspace._sector._cards_layout.count() >= 2  # two-column grid + stretch
    assert workspace._prompt_files_list.item(0).text() == "1. 情绪周期判断.txt"
    assert workspace._material_news_table.topLevelItemCount() == 2
    workspace._sent_source_button.setChecked(True)
    assert not workspace._sent_source_text.isHidden()
    assert "完整系统提示词" in workspace._sent_source_text.toPlainText()
    assert "完整用户原文" in workspace._sent_source_text.toPlainText()


def test_material_news_detail_uses_dark_surface_without_row_selection_highlight(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    workspace._populate_material_news_table(
        {
            "半导体": {
                "sector_code": "SH.BK0001",
                "items": [{"title": "测试消息", "snippet": "可读的正文内容"}],
            }
        }
    )
    message_row = workspace._material_news_table.topLevelItem(0).child(0)
    workspace._on_material_news_item_clicked(message_row, 1)

    detail_row = message_row.child(0)
    editor = workspace._material_news_table.itemWidget(detail_row, 1)
    assert isinstance(editor, QPlainTextEdit)
    assert "background: #11161D" in editor.styleSheet()
    assert "color: #E8ECF1" in editor.styleSheet()
    assert "background: transparent" in workspace._material_news_table.styleSheet()
    assert "background: transparent" in workspace._history_table.styleSheet()


def test_market_raw_payload_does_not_repeat_display_sections(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    marker = "MARKET_PARAGRAPH_MUST_APPEAR_ONCE"
    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    workspace._render_market_analysis(
        {
            "status": "ready",
            "source": "DSA analysis_history",
            "data": {
                "sections": [
                    {"title": "一、盘面总览", "markdown": marker}
                ]
            },
            "display_sections": [
                {"title": "一、盘面总览", "content": marker}
            ],
        },
        policy_environment=None,
    )

    raw_text = json.dumps(workspace._market._raw, ensure_ascii=False)
    assert raw_text.count(marker) == 1


def test_market_markdown_is_rendered_as_ordered_native_components(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    workspace._render_market_analysis(
        {
            "status": "ready",
            "source": "DSA",
            "data_date": "2026-08-16",
            "decision_date": "2026-08-16",
            "display_sections": [
                {"title": "四、资金与情绪", "content": "资金集中于局部主线。"},
                {"title": "七、风险提示", "content": "1. 高位题材补跌\n*注：不构成投资建议。*"},
                {
                    "title": "六、明日交易计划",
                    "content": "- **策略定性**：均衡\n- **仓位区间**：5成-7成\n- **关注方向**：半导体\n- **回避方向**：高位题材\n- **失效条件**：成交缩量",
                },
                {
                    "title": "一、盘面总览",
                    "content": "盘面偏暖但分化。\n\n- **盘面信号**：54/100\n- **信号依据**：涨跌分化\n\n| 指标 | 数值 |\n|---|---|\n| 上涨 | 2400 |",
                },
                {
                    "title": "二、指数结构",
                    "content": "成长指数较强。\n\n| 指数 | 涨跌幅 |\n|---|---|\n| 创业板指 | +1.12% |",
                },
                {"title": "五、消息催化", "content": "产业催化映射科技硬件。"},
            ],
        },
        policy_environment=None,
    )

    names = [
        label.text()
        for label in workspace._market.findChildren(QLabel, "secondOrderFieldName")
    ]
    plan_index = names.index("明日交易计划")
    assert plan_index < names.index("资金与情绪")
    assert plan_index < names.index("消息催化")
    assert plan_index < names.index("风险提示")
    assert len(workspace._market.findChildren(QTableWidget)) >= 2
    assert workspace._market.findChild(QLabel, "marketDisclaimer") is not None


def test_market_tab_exposes_manual_regeneration_and_half_day_note(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    calls: list[tuple[str, object]] = []
    workspace._start_worker = lambda operation, payload: calls.append((operation, payload))

    workspace._market_regenerate.click()

    assert calls == [
        (
            "market_refresh",
            {"dsa_database_path": workspace._dsa_database_edit.text().strip()},
        )
    ]
    assert any(
        "半天内程序仅自动生成一次" in label.text()
        for label in workspace.findChildren(QLabel)
    )


def test_history_tab_loads_records_when_opened_without_refresh_button(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    refresh_calls = []
    workspace._refresh_history = lambda: refresh_calls.append(workspace._symbol.text())

    toolbar = workspace._history.layout().itemAt(0).layout()
    assert toolbar is not None
    assert all(
        not (
            isinstance(toolbar.itemAt(index).widget(), QPushButton)
            and toolbar.itemAt(index).widget().text() == "刷新历史"
        )
        for index in range(toolbar.count())
    )
    workspace._tabs.setCurrentWidget(workspace._history)
    qtbot.wait(10)
    assert refresh_calls == [workspace._symbol.text()]


def test_second_order_open_and_fetch_refresh_the_dsa_cache(monkeypatch, qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui import second_order_workspace as module

    calls: list[str] = []

    def refresh(_context, payload):
        calls.append(str(payload.get("symbol") or ""))
        return {"status": "ready", "source": "DSA", "display_sections": []}

    monkeypatch.setattr(module, "_refresh_dsa_market_cache", refresh)
    workspace = module.SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    workspace.set_symbol("159732")
    qtbot.wait(10)
    workspace._start_worker = MagicMock()

    workspace._fetch_kline()

    assert calls == ["159732", "159732"]
    workspace._start_worker.assert_called_once_with(
        "kline", {"symbol": "159732", "timeframe": "120m"}
    )


def test_second_order_handoff_is_not_rendered_as_internal_json(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    workspace.set_pa_payload(
        {
            "symbol": "159732",
            "stock_name": "消费电子ETF华夏（SZ.159732）",
            "timeframe": "15m",
            "decision_point": "close",
            "should_trade": False,
            "settlement_mode": "t1",
            "data_source": "futu",
            "stage1_diagnosis": None,
            "stage2_decision": None,
        }
    )

    overview = workspace._overview.toPlainText()
    reasoning = workspace._game_reasoning.toPlainText()
    forbidden = ("stage1_diagnosis", "stage2_decision", "should_trade", "entry_price")
    assert "消费电子ETF华夏" in overview
    # 概览页渲染交接摘要字段（"等待运行"是博弈推演页的占位，不在这里）
    assert "PA 技术结论" in overview
    assert "等待运行" in reasoning
    assert all(field not in overview for field in forbidden)
    assert all(field not in reasoning for field in forbidden)


def test_game_reasoning_keeps_only_participant_and_behavior_cards(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    workspace._render_analysis_result(
        {
            "result": {
                "input": {"symbol": "159732", "materials": {"participant_priors": {}}},
                "scenario_tree": {
                    "analysis_metadata": {
                        "participant_analysis": {"participant": "主力"}
                    },
                    "branches": [
                        {
                            "name": "超预期强",
                            "a_class": {"主力": {"model_behavior": "拉升"}},
                            "b_class": {"gap_up": 0.5},
                            "c_class": {"stop_first": 0.2},
                        }
                    ],
                },
                "integrated_gates": {},
            }
        },
        refresh_history=False,
    )

    text = workspace._game_reasoning.toPlainText()
    assert "主导参与者行为推演" in text
    assert "B/C三情景概率" not in text
    assert "B 类概率含义" not in text
    assert "C 类概率含义" not in text


def test_second_order_overview_prioritizes_completed_analysis_conclusions(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    workspace.set_pa_payload({"symbol": "159732", "stock_name": "消费电子ETF华夏"})
    workspace._render_analysis_result(
        {
            "result": {
                "completed_at": "2026-08-13T15:01:00",
                "input": {
                    "symbol": "159732",
                    "decision_point": "close",
                    "cycle_position": "发酵",
                    "materials": {},
                },
                "scenario_tree": {
                    "analysis_metadata": {
                        "participant_analysis": {
                            "participant": "主力",
                            "key_evidence": ["量价同步", "板块资金回流"],
                        }
                    },
                    "branches": [
                        {
                            "name": "符合预期",
                            "status": "passed",
                            "a_class": {
                                "主力": {
                                    "model_behavior": "拉升",
                                    "probabilities": {"拉升": 0.6},
                                }
                            },
                        }
                    ],
                },
                "integrated_gates": {
                    "符合预期": {"status": "passed"},
                },
            }
        },
        refresh_history=False,
    )

    overview = workspace._overview.toPlainText()
    assert "消费电子ETF华夏" in overview
    assert "发酵" in overview
    assert "主力" in overview
    assert "拉升" in overview
    assert "量价同步" in overview
    assert "T+1" in overview
    assert "sector_belief" not in overview
    assert "game_signals" not in overview


def test_second_order_t1_panels_explain_missing_trade_plan_once(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    branches = [
        {
            "name": name,
            "status": "insufficient_data",
            "gate_reason": "缺少止盈价或止损价，无法计算 C 类首达概率；禁止新增买入",
            "action_advice": "B/C 类数据不足，禁止新增买入；仅保留制度允许的风险降低动作。",
        }
        for name in ("超预期强", "符合预期", "低于预期")
    ]
    gates = {
        name: {
            "mode": "T+1",
            "status": "insufficient_data",
            "pa_gate_passed": False,
            "second_order_gate_passed": False,
            "reason": "二阶数据不足，T+1 新增买入被阻断；PA 阶段 2 should_trade 原闸门结果",
        }
        for name in ("超预期强", "符合预期", "低于预期")
    }

    workspace._render_analysis_result(
        {
            "result": {
                "input": {
                    "symbol": "159732",
                    "materials": {
                        "probability_chain": {
                            "reason": "缺少止盈价或止损价，无法计算 C 类首达概率；禁止新增买入"
                        },
                        "position_cases": {},
                    },
                },
                "scenario_tree": {"branches": branches},
                "integrated_gates": gates,
            }
        },
        refresh_history=False,
    )

    tree = workspace._tree.toPlainText()
    gate = workspace._gate.toPlainText()
    assert "B/C三情景概率" in tree
    assert "该情景明天开盘概率" in tree
    assert "开盘首次下跌达止损概率" in tree
    assert "当前闸门结论" not in tree
    assert "缺少止盈价或止损价" not in tree
    assert "同一个二阶闸门结果同时作用于三个情景" not in tree
    assert "mode" not in tree
    assert "gate_passed" not in tree
    assert tree.count("二阶数据不足，T+1 新增买入被阻断") == 0
    assert "缺少的前置条件" in gate
    assert "缺少止盈价或止损价" in gate
    assert "PA 阶段 2 should_trade=false" in gate
    assert "second_order_status" not in gate
    assert "executable_actions" not in gate


def test_second_order_t1_panel_does_not_blame_pa_prices_for_sample_shortage(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    display = workspace._gate_display(
        [],
        {"probability_chain": {"reason": "匹配历史样本低于最小阈值"}},
        {"符合预期": {"status": "insufficient_data", "pa_gate_passed": True}},
        "匹配历史样本低于最小阈值",
    )

    assert "提供止盈价和止损价" not in display["下一步"]
    assert "历史样本" in display["下一步"]


def test_second_order_t1_panel_marks_new_buy_unevaluated_without_pa_signal(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    reason = "没有下单信号，T+1新增买入暂不评估"
    display = workspace._gate_display(
        [],
        {},
        {"符合预期": {"status": "not_applicable", "pa_gate_passed": False, "reason": reason}},
        reason,
    )

    assert display["结论"] == reason
    assert display["新增买入"] == "不评估"


def test_second_order_symbol_switch_clears_its_private_conversation(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    workspace.set_symbol("000001.SZ")
    workspace._chat_context.append({"role": "user", "content": "旧股票材料"})

    workspace.set_symbol("600519.SH")

    assert workspace._chat_context == []
    assert "品种已切换" in workspace._chat_transcript.toPlainText()


def test_second_order_sector_follows_symbol_and_news_keyword_setting_is_removed(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.config.settings import Settings
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    settings = Settings()
    settings.second_order.sector_name = "默认板块"
    settings.second_order.sector_code = "SH.BK0001"
    settings.second_order.symbol_preferences = {
        "SZ.159732": {
            "sector_name": "消费电子",
            "sector_code": "SZ.BK0002",
        },
        "SH.600519": {
            "sector_name": "白酒",
            "sector_code": "SH.BK0003",
        },
    }
    workspace = SecondOrderWorkspace(AppContext(settings=settings))
    qtbot.addWidget(workspace)

    workspace.set_symbol("SZ.159732")
    assert workspace._sector_name_edit.text() == "消费电子"
    assert workspace._sector_code_edit.text() == "SZ.BK0002"
    workspace.set_symbol("SH.600519")
    assert workspace._sector_name_edit.text() == "白酒"
    assert workspace._sector_code_edit.text() == "SH.BK0003"
    workspace.set_symbol("000001.SZ")
    assert workspace._sector_name_edit.text() == ""
    assert workspace._sector_code_edit.text() == ""
    assert not hasattr(workspace, "_news_keyword_edit")


def test_second_order_data_settings_persist_separately_for_each_symbol(
    qtbot, tmp_path, monkeypatch
) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.config import paths
    from pa_agent.config.settings import Settings, load_settings
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(paths, "SETTINGS_JSON_PATH", settings_path)
    initial = Settings()
    first_workspace = SecondOrderWorkspace(
        AppContext(settings=initial.model_copy(deep=True))
    )
    second_workspace = SecondOrderWorkspace(
        AppContext(settings=initial.model_copy(deep=True))
    )
    qtbot.addWidget(first_workspace)
    qtbot.addWidget(second_workspace)

    first_workspace.set_symbol("159732.SZ")
    first_workspace._sector_name_edit.setText("消费电子")
    first_workspace._sector_code_edit.setText("SH.LIST0022")
    first_workspace._save_data_settings()

    second_workspace.set_symbol("600519.SH")
    second_workspace._sector_name_edit.setText("白酒")
    second_workspace._sector_code_edit.setText("SH.BK0003")
    second_workspace._save_data_settings()

    reloaded = SecondOrderWorkspace(AppContext(settings=load_settings(settings_path)))
    qtbot.addWidget(reloaded)
    reloaded.set_symbol("SZ.159732")
    assert reloaded._sector_name_edit.text() == "消费电子"
    assert reloaded._sector_code_edit.text() == "SH.LIST0022"
    reloaded.set_symbol("SH.600519")
    assert reloaded._sector_name_edit.text() == "白酒"
    assert reloaded._sector_code_edit.text() == "SH.BK0003"


def test_second_order_settings_use_terminal_symbol_before_payload_sync(
    qtbot, tmp_path, monkeypatch
) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.config import paths
    from pa_agent.config.settings import Settings, load_settings
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(paths, "SETTINGS_JSON_PATH", settings_path)
    settings = Settings()
    settings.general.last_symbol = "SZ.159865"
    workspace = SecondOrderWorkspace(AppContext(settings=settings))
    qtbot.addWidget(workspace)

    assert workspace._last_symbol == "SZ.159865"
    workspace._sector_code_edit.setText("SH.BK0099")
    workspace._save_data_settings()

    persisted = load_settings(settings_path).second_order.symbol_preferences
    assert persisted["SZ.159865"]["sector_code"] == "SH.BK0099"


def test_second_order_material_payload_preserves_sector_code(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.config.settings import Settings
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    settings = Settings()
    settings.second_order.symbol_preferences = {
        "SH.600519": {
            "sector_name": "白酒",
            "sector_code": "HK.HSI Constituent",
        }
    }
    workspace = SecondOrderWorkspace(AppContext(settings=settings))
    qtbot.addWidget(workspace)
    workspace.set_symbol("600519.SH")

    payload = workspace._material_payload()

    assert payload["sector_code"] == "HK.HSI Constituent"


def test_second_order_analysis_payload_contains_normalized_sector_code() -> None:
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    class TextControl:
        def __init__(self, value: str) -> None:
            self.value = value

        def text(self) -> str:
            return self.value

    class Toggle:
        def isChecked(self) -> bool:
            return True

    class Workspace:
        _workers = set()
        _payload = {}
        _chat_context = []
        _symbol = TextControl("SZ.159732")
        _sector_name_edit = TextControl("消费电子")
        _sector_code_edit = TextControl("US.SEMICONDUCTORS")
        _dsa_database_edit = TextControl("")
        _run_news_prefetch_toggle = Toggle()
        _run_material_preanalysis_toggle = Toggle()

        @staticmethod
        def _update_llm_trace(_trace) -> None:
            pass

    captured = {}

    def start_worker(operation, payload) -> None:
        captured.update(operation=operation, payload=dict(payload))

    workspace = Workspace()
    workspace._start_worker = start_worker

    SecondOrderWorkspace._start_analysis_worker(workspace)

    assert captured["operation"] == "analysis"
    assert captured["payload"]["sector_code"] == "US.SEMICONDUCTORS"


def test_second_order_run_analysis_fetches_kline_first() -> None:
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    class Workspace:
        _workers = set()
        _pending_analysis = False
        fetched = False

        @staticmethod
        def _update_llm_trace(_trace) -> None:
            pass

        def _fetch_kline(self) -> None:
            self.fetched = True

    workspace = Workspace()

    SecondOrderWorkspace._run_analysis(workspace)

    assert workspace.fetched is True
    assert workspace._pending_analysis is True


def test_second_order_run_tooltip_depends_on_pa_analysis_completion(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)

    workspace.set_pa_payload(
        {"symbol": "159732", "stage1_diagnosis": None, "stage2_decision": None}
    )
    assert workspace._run.toolTip() == "强烈推荐先完成 PA 技术分析"

    workspace.set_pa_payload(
        {
            "symbol": "159732",
            "stage1_diagnosis": {"trend": "上升"},
            "stage2_decision": {"decision": {"order_type": "买入"}},
        }
    )
    assert workspace._run.toolTip() == ""


def test_second_order_missing_sector_code_error_explains_settings_recovery() -> None:
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    message = SecondOrderWorkspace._worker_error_message(
        "运行前材料预分析失败：PA 设置缺少必填的 sector_code"
    )

    assert "二阶博弈 → 设置" in message
    assert "富途返回的板块代码" in message
    assert "保存设置" in message


def test_second_order_sentiment_display_distinguishes_unavailable_from_closed() -> None:
    from pa_agent.gui.second_order_workspace import _sentiment_index_display

    assert _sentiment_index_display(
        50.0, {"status": "market_data_unavailable"}
    ) == "数据源不可用（保持值 50.0）"
    assert _sentiment_index_display(
        50.0, {"status": "non_trading_day"}
    ) == "休市（保持值 50.0）"


def test_second_order_material_preanalysis_failure_shows_settings_guidance(
    monkeypatch, qtbot
) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.second_order_workspace import SecondOrderWorkspace

    workspace = SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    warnings: list[str] = []
    monkeypatch.setattr(
        "pa_agent.gui.second_order_workspace.QMessageBox.warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    workspace._worker_succeeded(
        "material_preanalysis",
        {
            "ok": False,
            "error": "PA 设置缺少必填的 sector_code",
        },
    )

    assert warnings and "二阶博弈 → 设置" in warnings[0]
    assert "富途返回的板块代码" in warnings[0]
    assert "材料预分析完成" not in workspace._status.text()


def test_second_order_creates_a_fresh_pa_client_instead_of_using_context_client(
    monkeypatch,
) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.config.settings import Settings
    from pa_agent.gui import second_order_workspace as module

    reused_pa_client = object()
    isolated_client = object()
    captured = {}

    class Adapter:
        def __init__(self, client, *, provider, activity_callback=None, token_callback=None):
            captured["client"] = client
            captured["provider"] = provider
            captured["activity_callback"] = activity_callback
            captured["token_callback"] = token_callback

    class MarketAdapter:
        def __init__(
            self, source, *, news_fallback_provider=None, sector_market_source=None,
            max_news_items=None,
        ):
            captured["source"] = source
            captured["news_fallback"] = news_fallback_provider
            captured["sector_market_source"] = sector_market_source
            captured["max_news_items"] = max_news_items

    class Service:
        def __init__(self, *, market_source, model_client, **kwargs):
            captured["market_source"] = market_source
            captured["model_client"] = model_client
            captured["dsa_runtime_enabled"] = kwargs.get("dsa_runtime_enabled")

    context = AppContext(settings=Settings(), client=reused_pa_client)
    monkeypatch.setattr(
        "pa_agent.ai.client_factory.create_ai_client",
        lambda *_args, **_kwargs: isolated_client,
    )
    monkeypatch.setattr(
        module,
        "_load_second_order_modules",
        lambda: (Adapter, Service, MarketAdapter),
    )

    quote_context = object()
    market_source = type("MarketSource", (), {"_context": quote_context})()
    module._embedded_service(context, market_source)

    assert captured["client"] is isolated_client
    assert captured["client"] is not reused_pa_client
    assert captured["provider"] == "PA_Agent.second_order"
    assert captured["dsa_runtime_enabled"] is True
    assert captured["sector_market_source"] is not None
    assert captured["sector_market_source"]._quote_context is quote_context
    assert captured["max_news_items"] == 18


@pytest.mark.parametrize(
    ("news_enabled", "preanalysis_enabled", "expected"),
    [
        (False, False, ["dsa", "prefetch:平安银行", "preanalysis", "analysis", "service_close", "disconnect"]),
        (True, True, ["dsa", "prefetch:平安银行", "preanalysis", "analysis", "service_close", "disconnect"]),
    ],
)
def test_second_order_analysis_worker_requires_prefetch_and_material_preanalysis(
    monkeypatch, news_enabled, preanalysis_enabled, expected,
) -> None:
    from pa_agent.gui import second_order_workspace as module

    calls: list[str] = []

    class Source:
        def disconnect(self):
            calls.append("disconnect")

    class Service:
        def close(self):
            calls.append("service_close")

        def ensure_market_material(self, _payload):
            calls.append("dsa")
            return {"status": "ready", "market": {"status": "ready"}}

        def prefetch_news(self, sectors, *, search_keywords):
            calls.append(f"prefetch:{search_keywords[sectors[0]]}")
            return {"ok": True}

        def prepare_materials(self, _payload):
            calls.append("preanalysis")
            return {"ok": True}

        def run_analysis(self, _payload):
            calls.append("analysis")
            return {"ok": True, "result": {}}

    monkeypatch.setattr(module, "_create_market_source", lambda *_args: Source())
    monkeypatch.setattr(module, "_embedded_service", lambda *_args, **_kwargs: Service())
    worker = module._ApiWorker(
        object(),
        "analysis",
        {
            "symbol": "000001.SZ",
            "stock_name": "平安银行",
            "run_news_prefetch_enabled": news_enabled,
            "run_material_preanalysis_enabled": preanalysis_enabled,
        },
    )

    worker.run()

    assert calls == expected


def test_second_order_news_prefetch_worker_forwards_sector_code(monkeypatch) -> None:
    from pa_agent.gui import second_order_workspace as module

    captured: dict[str, object] = {}

    class Source:
        def disconnect(self):
            pass

    class Service:
        def prefetch_news(self, sectors, *, search_keywords, sector_codes):
            captured["sectors"] = sectors
            captured["search_keywords"] = search_keywords
            captured["sector_codes"] = sector_codes
            return {"ok": True}

    monkeypatch.setattr(module, "_create_market_source", lambda *_args: Source())
    monkeypatch.setattr(module, "_embedded_service", lambda *_args: Service())
    worker = module._ApiWorker(
        object(),
        "news_prefetch",
        {
            "symbol": "600519.SH",
            "sector_name": "白酒",
            "sector_code": "sh.bk0001",
        },
    )

    worker.run()

    assert captured == {
        "sectors": ("白酒",),
        "search_keywords": {"白酒": "白酒"},
        "sector_codes": {"白酒": "sh.bk0001"},
    }


def test_t1_stage2_completion_auto_routes_the_current_decision(
    monkeypatch, qtbot
) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui import second_order_workspace as module
    from pa_agent.gui.main_window import MainWindow

    monkeypatch.setattr(
        module,
        "_refresh_dsa_market_cache",
        lambda *_args: {"status": "not_configured"},
    )
    window = MainWindow(AppContext(), embedded=True)
    qtbot.addWidget(window)
    window._symbol_combo.setCurrentText("159732")
    workspace = window._second_order_workspace
    workspace.set_pa_payload = MagicMock()
    workspace.run_automatic_analysis = MagicMock()
    current = {"decision": {"order_type": "限价单", "entry_price": 10.2}}

    window._sync_second_order_after_stage2(current)

    payload = workspace.set_pa_payload.call_args.args[0]
    assert payload["stage2_decision"] is current
    assert payload["settlement_mode"] == "t1"
    assert window._strategy_browser.currentIndex() == 1
    workspace.run_automatic_analysis.assert_called_once_with()


def test_t1_stage2_no_order_only_synchronizes_without_running(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.main_window import MainWindow

    window = MainWindow(AppContext(), embedded=True)
    qtbot.addWidget(window)
    window._symbol_combo.setCurrentText("159732")
    workspace = window._second_order_workspace
    workspace.set_pa_payload = MagicMock()
    workspace.run_automatic_analysis = MagicMock()

    window._sync_second_order_after_stage2(
        {"decision": {"order_type": "不下单", "entry_price": None}}
    )

    payload = workspace.set_pa_payload.call_args.args[0]
    assert payload["settlement_mode"] == "t1"
    assert payload["should_trade"] is False
    assert window._strategy_browser.currentIndex() == 0
    workspace.run_automatic_analysis.assert_not_called()


def test_t0_stage2_completion_only_synchronizes_without_running(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.main_window import MainWindow

    window = MainWindow(AppContext(), embedded=True)
    qtbot.addWidget(window)
    window._symbol_combo.setCurrentText("513090")
    workspace = window._second_order_workspace
    workspace.set_pa_payload = MagicMock()
    workspace.run_automatic_analysis = MagicMock()

    window._sync_second_order_after_stage2({"decision": {"order_type": "限价单"}})

    workspace.set_pa_payload.assert_called_once()
    workspace.run_automatic_analysis.assert_not_called()


def test_second_order_settings_open_dedicated_history_folder(
    qtbot, monkeypatch, tmp_path
) -> None:
    from pa_agent.app_context import AppContext
    import pa_agent.gui.second_order_workspace as module

    workspace = module.SecondOrderWorkspace(AppContext())
    qtbot.addWidget(workspace)
    opened: list[str] = []
    monkeypatch.setattr(module, "second_order_root", lambda: tmp_path)
    monkeypatch.setattr(module.os, "startfile", lambda value: opened.append(value))

    workspace._open_resource("history")

    assert opened == [str(tmp_path / "analysis_history")]
    assert (tmp_path / "analysis_history").is_dir()


def test_opening_embedded_analysis_does_not_flash_child_windows(qtbot, qapp) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.gui.main_window import MainWindow

    shown_top_level_terminals: list[str] = []

    class ShowProbe(QObject):
        def eventFilter(self, watched, event):  # noqa: N802
            if (
                event.type() == QEvent.Type.Show
                and isinstance(watched, MainWindow)
                and watched.isWindow()
            ):
                shown_top_level_terminals.append(watched.metaObject().className())
            return False

    probe = ShowProbe()
    qapp.installEventFilter(probe)
    try:
        host_tabs = QTabWidget()
        qtbot.addWidget(host_tabs)
        window = MainWindow(AppContext(), parent=host_tabs, embedded=True)
        assert not window.isWindow()
        history_table = window._ai_sidebar.history._table
        assert (
            history_table.verticalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        host_tabs.addTab(window, "测试标的")
        host_tabs.show()
        qapp.processEvents()
        qtbot.wait(100)
        assert (
            history_table.verticalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
    finally:
        qapp.removeEventFilter(probe)

    assert shown_top_level_terminals == []


def test_analysis_window_restores_saved_chart_split_hotzone_ratio(qtbot, qapp) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.config.settings import Settings
    from pa_agent.gui.main_window import MainWindow

    context = AppContext(settings=Settings())
    context.settings.general.split_hotzone_ratios["chart_indicator"] = 0.63
    window = MainWindow(context, embedded=True)
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    qapp.processEvents()

    sizes = window._chart_indicator_splitter.sizes()
    assert sum(sizes) > 0
    assert 0.60 <= sizes[0] / sum(sizes) <= 0.66


def test_analysis_window_restores_saved_workbench_split_ratio(qtbot, qapp) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.config.settings import Settings
    from pa_agent.gui.main_window import MainWindow

    context = AppContext(settings=Settings())
    context.settings.general.split_hotzone_ratios["workbench"] = 0.58
    window = MainWindow(context, embedded=True)
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    qapp.processEvents()

    sizes = window._workbench.sizes()
    assert sum(sizes) > 0
    assert 0.55 <= sizes[0] / sum(sizes) <= 0.61


def test_embedded_terminal_respects_watchlist_timeframe_including_30m(qtbot) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.config.settings import Settings
    from pa_agent.gui.main_window import MainWindow

    class _FakeSource:
        def supported_timeframes(self):
            return ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]

    for timeframe in ("15m", "1h", "30m"):
        settings = Settings()
        settings.general.last_timeframe = timeframe
        context = AppContext(settings=settings, data_source=_FakeSource())
        window = MainWindow(context, embedded=True)
        qtbot.addWidget(window)
        assert window._tf_combo.currentText() == timeframe
        window.close()


def test_theme_removes_tab_drag_indicator_and_uses_one_pixel_action_borders() -> None:
    from pa_agent.gui.theme.apply import _QSS_PATH

    qss = _QSS_PATH.read_text(encoding="utf-8")

    assert "QTabBar::tear { width: 0px; border: none; background: transparent; }" in qss
    assert "border: 2px solid #333A45" not in qss
    assert "border-width: 2px" not in qss
    assert "QToolButton#studyMenuButton::menu-indicator" in qss
    assert "border-top: 1px solid #22272F" in qss
    assert "QToolButton#indicatorSettingsButton {" in qss


def test_second_order_labeler_catchup_worker_provides_real_market_source(
    monkeypatch,
) -> None:
    """labeler_catchup 必须向 _embedded_service 传真实行情源，而非 None。

    回归用例：PAMarketDataAdapter 构造时强制要求数据源暴露
    latest_snapshot(n)，传 None 会抛
    TypeError("PA data source must expose latest_snapshot(n)")。
    """
    from pa_agent.gui import second_order_workspace as module

    captured: dict[str, object] = {}
    calls: list[str] = []

    class Source:
        def disconnect(self):
            calls.append("disconnect")

    class Service:
        def close(self):
            calls.append("service_close")

        def labeler_status(self):
            class _Status:
                def to_dict(self):
                    return {"load_state": "loaded"}

            return _Status()

    def fake_create_source(_context, symbol):
        captured["symbol"] = symbol
        return Source()

    def fake_embedded_service(_context, market_source, **_kwargs):
        captured["market_source"] = market_source
        return Service()

    monkeypatch.setattr(module, "_create_market_source", fake_create_source)
    monkeypatch.setattr(module, "_embedded_service", fake_embedded_service)
    worker = module._ApiWorker(
        object(),
        "labeler_catchup",
        {"symbol": "600519.SH"},
    )

    worker.run()

    assert captured["symbol"] == "600519.SH"
    assert isinstance(captured["market_source"], Source)
    assert calls == ["service_close", "disconnect"]
