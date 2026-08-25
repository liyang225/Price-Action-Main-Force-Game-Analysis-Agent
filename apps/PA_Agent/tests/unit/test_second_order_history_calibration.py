"""History-tab rendering contract for offline probability calibration."""

from __future__ import annotations

from PyQt6.QtWidgets import QTableWidget

from pa_agent.gui.second_order_workspace import _CalibrationSummaryPanel


def test_calibration_panel_renders_brier_and_prior_direction(qtbot) -> None:
    panel = _CalibrationSummaryPanel()
    qtbot.addWidget(panel)
    panel.set_summary(
        {
            "status": "available",
            "minimum_sample_count": 30,
            "prediction_count": 120,
            "resolved_prediction_count": 90,
            "reports": [
                {
                    "status": "available",
                    "probability_type": "B",
                    "outcome": "gap_up",
                    "decision_point": "午盘",
                    "config_version": 2,
                    "sample_count": 30,
                    "brier_score": 0.08421,
                    "prior_adjustment_direction": "increase",
                }
            ],
        }
    )

    table = panel.findChild(QTableWidget, "secondOrderCalibrationTable")
    assert table is not None
    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "B · 超预期强"
    assert table.item(0, 3).text() == "0.0842"
    assert table.item(0, 4).text() == "增加该结果先验"
    assert "回填 90 / 120" in panel._status.text()


def test_calibration_panel_explains_insufficient_and_empty_states(qtbot) -> None:
    panel = _CalibrationSummaryPanel()
    qtbot.addWidget(panel)
    panel.set_summary(
        {
            "status": "insufficient_data",
            "minimum_sample_count": 30,
            "prediction_count": 9,
            "resolved_prediction_count": 3,
            "reports": [
                {
                    "status": "insufficient_data",
                    "probability_type": "B",
                    "outcome": "near_reference",
                    "decision_point": "收盘",
                    "config_version": 2,
                    "sample_count": 1,
                }
            ],
        }
    )

    assert panel._table.item(0, 3).text() == "样本不足"
    assert panel._table.item(0, 4).text() == "待评估"
    assert "每个结果满 30 次" in panel._status.text()

    panel.set_summary({"status": "no_data", "reports": []})
    assert panel._table.rowCount() == 0
    assert "完成一次二阶分析后开始记录" in panel._status.text()
