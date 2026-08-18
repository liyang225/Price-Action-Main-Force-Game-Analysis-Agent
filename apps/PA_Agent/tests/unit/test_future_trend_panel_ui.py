from __future__ import annotations


def test_structured_reasoning_does_not_highlight_numbers(qtbot) -> None:
    from pa_agent.gui.future_trend_panel import FutureTrendPanel

    panel = FutureTrendPanel()
    qtbot.addWidget(panel)
    panel.set_prediction(
        {
            "next_cycle_prediction": {
                "direction": "bullish",
                "probabilities": {"broad_channel": 35},
                "reasoning": "核心形成因：突破 1.42。\n汇总评估：宽通道概率 35%。",
            }
        }
    )

    assert "1.42" in panel._cycle_reasoning_edit.toPlainText()
    assert "35%" in panel._cycle_reasoning_edit.toPlainText()
    assert "#38bdf8" not in panel._cycle_reasoning_edit.toHtml().lower()
    assert panel._cycle_direction_label.text().startswith("方向：")


def test_structured_reasoning_breaks_after_sentence_punctuation(qtbot) -> None:
    from pa_agent.gui.future_trend_panel import FutureTrendPanel

    panel = FutureTrendPanel()
    qtbot.addWidget(panel)
    panel.set_prediction(
        {
            "next_cycle_prediction": {
                "direction": "bullish",
                "probabilities": {"broad_channel": 100},
                "reasoning": "价格突破 1.42。回落至 1.398 则路径失效。",
            }
        }
    )

    text = panel._cycle_reasoning_edit.toPlainText()
    assert "• 价格突破 1.42。\n• 回落至 1.398" in text


def test_probability_numbers_only_highlight_the_maximum_in_blue(qtbot) -> None:
    from pa_agent.gui.future_trend_panel import FutureTrendPanel
    from pa_agent.gui.theme import tokens as T

    panel = FutureTrendPanel()
    qtbot.addWidget(panel)
    panel.set_prediction(
        {
            "next_bar_prediction": {
                "probabilities": {"bullish": 61, "bearish": 24, "neutral": 15},
                "reasoning": "测试",
            },
            "next_cycle_prediction": {
                "direction": "bullish",
                "probabilities": {
                    "spike": 42,
                    "tight_channel": 25,
                    "broad_channel": 18,
                },
                "reasoning": "测试",
            },
        }
    )

    bar_html = panel._bar_direction_label.text().lower()
    assert f"color:{T.ACCENT.lower()};'>61%" in bar_html
    assert f"color:{T.FG.lower()};'>24%" in bar_html
    assert f"color:{T.FG.lower()};'>15%" in bar_html
    assert T.MKT_UP.lower() not in bar_html
    assert T.MKT_DOWN.lower() not in bar_html
    assert f"color:{T.ACCENT.lower()}" in panel._chip_labels[0].text().lower()
    assert f"color:{T.FG.lower()}" in panel._chip_labels[1].text().lower()
