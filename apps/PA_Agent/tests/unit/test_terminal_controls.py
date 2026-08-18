from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")


def test_pipeline_strip_is_compact_and_tracks_status(qtbot) -> None:
    from pa_agent.gui.widgets.flow_bar import FlowBar

    bar = FlowBar()
    qtbot.addWidget(bar)

    assert bar.height() == 32
    bar.set_step_status(2, "active")
    bar.set_step_caption(2, "诊断中 (12.4s)")

    assert bar._steps[2].status == "active"
    assert bar._steps[2]._dot.text() == "●"
    assert not hasattr(bar._steps[2], "_caption")


def test_pipeline_last_node_name_is_not_clipped(qtbot, qapp) -> None:
    from pa_agent.gui.widgets.flow_bar import FlowBar

    bar = FlowBar()
    bar.setFixedWidth(370)
    qtbot.addWidget(bar)
    bar.show()
    qapp.processEvents()

    for step in bar._steps:
        assert step._name.width() >= step._name.sizeHint().width()
    assert bar.width() == 370


def test_analysis_sidebar_pane_shares_the_tab_bottom_border(qtbot) -> None:
    from pa_agent.gui.ai_sidebar import AISidebar
    from pa_agent.gui.theme.apply import _QSS_PATH

    sidebar = AISidebar()
    qtbot.addWidget(sidebar)

    assert sidebar._tabs.objectName() == "analysisSidebarTabs"
    qss = _QSS_PATH.read_text(encoding="utf-8")
    assert "QTabWidget#analysisSidebarTabs::pane {\n    top: -1px;\n    margin-bottom: 0;\n}" in qss


def test_future_trend_tab_is_immediately_after_decision(qtbot) -> None:
    from pa_agent.gui.ai_sidebar import AISidebar

    sidebar = AISidebar()
    qtbot.addWidget(sidebar)

    assert sidebar._tabs.tabText(sidebar.TAB_DECISION) == "决策"
    assert sidebar._tabs.tabText(sidebar.TAB_FUTURE_TREND) == "未来走势预期"
    assert sidebar.TAB_FUTURE_TREND == sidebar.TAB_DECISION + 1



def test_toggle_switch_emits_checkbox_compatible_state(qtbot) -> None:
    from PyQt6.QtCore import Qt

    from pa_agent.gui.widgets.toggle_switch import ToggleSwitch

    switch = ToggleSwitch()
    qtbot.addWidget(switch)
    observed: list[int] = []
    switch.stateChanged.connect(observed.append)

    switch.click()

    assert switch.isChecked()
    assert observed == [Qt.CheckState.Checked.value]
    qtbot.waitUntil(lambda: switch.thumbOffset == pytest.approx(1.0), timeout=750)
    assert switch.sizeHint().width() == 48
    assert switch.sizeHint().height() == 24


def test_shimmer_button_paints_when_tracking_is_active(qtbot) -> None:
    from PyQt6.QtCore import Qt

    from pa_agent.gui.widgets.shimmer_button import ShimmerButton

    button = ShimmerButton()
    button.set_tracking_labels(
        active="持续分析",
        inactive="持续分析",
    )
    qtbot.addWidget(button)
    button.resize(132, 34)
    assert button.text() == "持续分析"
    button.setChecked(True)
    button.show()

    qtbot.waitUntil(lambda: button.thumbOffset == pytest.approx(1.0), timeout=750)

    image = button.grab()

    assert not image.isNull()
    assert button.text() == "持续分析"
    assert button.sizeHint().width() > 48
    assert button.sizeHint().height() == 26
    observed: list[int] = []
    button.stateChanged.connect(observed.append)
    button.click()
    assert observed == [Qt.CheckState.Unchecked.value]
    assert button.text() == "持续分析"


def test_settings_dialogs_use_standard_toggle_switches(qtbot) -> None:
    from PyQt6.QtWidgets import QCheckBox

    from pa_agent.config.settings import Settings
    from pa_agent.gui.ai_model_settings_dialog import AIModelSettingsDialog
    from pa_agent.gui.feishu_settings_dialog import FeishuSettingsDialog
    from pa_agent.gui.settings_dialog import SettingsDialog
    from pa_agent.gui.widgets.toggle_switch import ToggleSwitch

    settings = Settings()
    dialogs = [
        AIModelSettingsDialog(settings),
        FeishuSettingsDialog(settings),
        SettingsDialog(settings),
    ]
    for dialog in dialogs:
        qtbot.addWidget(dialog)
        assert not dialog.findChildren(QCheckBox)

    assert isinstance(dialogs[0]._thinking_check, ToggleSwitch)
    assert isinstance(dialogs[1]._enabled_check, ToggleSwitch)
    assert isinstance(dialogs[2]._flow_auto_play_check, ToggleSwitch)
