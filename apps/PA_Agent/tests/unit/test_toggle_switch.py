"""Tests for custom settings switches."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")


def test_signal_blocked_initial_load_updates_toggle_visual_state(qtbot) -> None:
    from pa_agent.gui.widgets.toggle_switch import ToggleSwitch

    switch = ToggleSwitch()
    qtbot.addWidget(switch)
    switch.blockSignals(True)
    switch.setChecked(True)
    switch.blockSignals(False)

    assert switch.isChecked()
    assert switch.thumbOffset == 1.0
    assert switch.trackProgress == 1.0


def test_general_settings_restores_enabled_order_alert_visually(qtbot) -> None:
    from pa_agent.config.settings import Settings
    from pa_agent.gui.general_settings_dialog import GeneralSettingsDialog

    settings = Settings()
    settings.general.alert_on_order_opportunity = True
    dialog = GeneralSettingsDialog(settings)
    qtbot.addWidget(dialog)

    assert dialog._alert_on_order_check.isChecked()
    assert dialog._alert_on_order_check.thumbOffset == 1.0
    assert dialog._alert_on_order_check.trackProgress == 1.0


def test_general_settings_has_no_default_kline_timeframe_control(qtbot) -> None:
    from pa_agent.config.settings import Settings
    from pa_agent.gui.general_settings_dialog import GeneralSettingsDialog

    dialog = GeneralSettingsDialog(Settings())
    qtbot.addWidget(dialog)

    assert not hasattr(dialog, "_default_timeframe_combo")
