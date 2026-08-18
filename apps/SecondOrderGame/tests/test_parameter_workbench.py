"""Critical Qt interactions for the HMM parameter workbench."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytest.importorskip("PyQt6")
from PyQt6.QtCore import Qt

from src.gui.session import RowRef
from src.gui.workbench import ParameterWorkbench


CONFIG_PATH = Path(__file__).parents[1] / "config" / "hmm_prior.yaml"


def test_workbench_opens_without_writing_and_keeps_the_disclaimer_visible(qtbot, parameter_config_path) -> None:
    original = parameter_config_path.read_bytes()
    window = ParameterWorkbench(parameter_config_path)
    qtbot.addWidget(window)
    window.show()

    assert window.minimumWidth() == 960
    assert window.minimumHeight() == 620
    assert window.disclaimer_label.isVisible()
    assert "专家先验推演，非统计估计" in window.disclaimer_label.text()
    assert parameter_config_path.read_bytes() == original


def test_preview_controls_are_clearly_marked_as_non_production_simulation(
    qtbot, parameter_config_path
) -> None:
    window = ParameterWorkbench(parameter_config_path)
    qtbot.addWidget(window)

    assert window.preview_scope_label.text() == (
        "仅用于参数效果预览，不影响生产运行，也不会随配置保存"
    )
    assert window.observation_label.text() == "模拟观测"
    assert window.policy_label.text() == "模拟政策环境"


def test_beginner_slider_updates_the_shared_draft_and_live_preview(qtbot, parameter_config_path) -> None:
    window = ParameterWorkbench(parameter_config_path)
    qtbot.addWidget(window)
    window.show()
    window.show_beginner_row(RowRef("transition_matrix", "冰点"))

    window.beginner_sliders["启动"].setValue(60)

    row = window.session.config["transition_matrix"]["冰点"]
    assert row["启动"] == pytest.approx(0.60)
    assert sum(value for key, value in row.items() if key != "alpha") == pytest.approx(1.0)
    assert "未保存" in window.session_state_label.text()
    assert window.preview_status_label.text() == "预览已同步"


def test_invalid_expert_cell_disables_save_and_suppresses_preview(qtbot, parameter_config_path) -> None:
    window = ParameterWorkbench(parameter_config_path)
    qtbot.addWidget(window)
    window.show()
    table = window.expert_tables["A"]

    table.item(0, 1).setText("不是数字")

    assert window.validation_label.isVisible()
    assert "有限数字" in window.validation_label.text()
    assert not window.save_button.isEnabled()
    assert "预览暂停" in window.preview_status_label.text()

    table.item(1, 0).setText("0.050")

    assert not window.save_button.isEnabled()
    assert "仍有无法解析" in window.validation_label.text()


def test_expert_workbench_shows_distinct_main_force_and_retail_columns(qtbot, parameter_config_path) -> None:
    window = ParameterWorkbench(parameter_config_path)
    qtbot.addWidget(window)

    main_headers = [
        window.expert_tables["W · 主力"].horizontalHeaderItem(index).text()
        for index in range(window.expert_tables["W · 主力"].columnCount() - 1)
    ]
    retail_headers = [
        window.expert_tables["W · 散户"].horizontalHeaderItem(index).text()
        for index in range(window.expert_tables["W · 散户"].columnCount() - 1)
    ]

    assert main_headers == ["建仓", "震仓", "拉升", "出货", "观望", "狩猎止损"]
    assert retail_headers == ["FOMO追高", "恐慌割肉", "观望", "理性跟随", "底部建仓", "高位减仓"]


def test_user_can_supply_the_current_sector_belief_for_preview(qtbot, parameter_config_path) -> None:
    window = ParameterWorkbench(parameter_config_path)
    qtbot.addWidget(window)
    window.show()

    for state, value in {
        "冰点": 100.0,
        "启动": 0.0,
        "发酵": 0.0,
        "高潮": 0.0,
        "退潮": 0.0,
    }.items():
        window.belief_inputs[state].setValue(value)

    assert window._preview_belief["冰点"] == pytest.approx(1.0)
    assert window.preview_status_label.text() == "预览已同步"


def test_save_button_increments_version_and_refreshes_history(qtbot, parameter_config_path) -> None:
    window = ParameterWorkbench(parameter_config_path)
    qtbot.addWidget(window)
    window.show()
    window.show_beginner_row(RowRef("transition_matrix", "冰点"))
    window.beginner_sliders["启动"].setValue(40)

    qtbot.mouseClick(window.save_button, Qt.MouseButton.LeftButton)

    saved = yaml.safe_load(parameter_config_path.read_text(encoding="utf-8"))
    assert saved["version"] == 6
    assert not window.session.is_dirty
    assert window.history_list.count() >= 2
    assert "已保存 v6" in window.session_state_label.text()
