from __future__ import annotations


def test_decision_diagnosis_shows_cycle_support_and_resistance(qtbot) -> None:
    from pa_agent.gui.decision_panel import DecisionPanel

    panel = DecisionPanel()
    qtbot.addWidget(panel)
    panel.set_decision(
        {
            "order_type": "不下单",
            "reasoning": "第一句。第二句。",
            "next_cycle_prediction": {
                "cycle": "broad_channel",
                "direction": "bullish",
                "unpredictable": False,
            },
        },
        diagnosis_summary={"cycle_position": "normal_channel", "direction": "bullish"},
        stage1_diagnosis={
            "support_levels": ["1.398", "1.376"],
            "resistance_levels": ["1.42"],
        },
    )

    assert panel._next_cycle_label.text() == "下一市场周期：上涨宽通道"
    assert panel._support_label.text() == "支撑（由近及远）：1.398 / 1.376"
    assert panel._resistance_label.text() == "阻力（由近及远）：1.42"
    assert "background-color: #181C22" in panel._next_cycle_label.styleSheet()
    assert "color: #E8ECF1" in panel._next_cycle_label.styleSheet()
    assert "background-color: #181C22" in panel._trend_label.styleSheet()
    assert "border: 1px solid #22272F" in panel._trend_label.styleSheet()
    assert panel._reasoning_edit.toPlainText() == "• 第一句。\n• 第二句。"


def test_decision_context_labels_reset_to_empty(qtbot) -> None:
    from pa_agent.gui.decision_panel import DecisionPanel

    panel = DecisionPanel()
    qtbot.addWidget(panel)
    panel.clear()

    assert panel._next_cycle_label.text() == "下一市场周期：—"
    assert panel._support_label.text() == "支撑（由近及远）：—"
    assert panel._resistance_label.text() == "阻力（由近及远）：—"
