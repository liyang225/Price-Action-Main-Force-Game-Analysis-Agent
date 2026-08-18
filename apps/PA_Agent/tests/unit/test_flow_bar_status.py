from __future__ import annotations


def test_active_pipeline_step_uses_blue_pulse(qtbot) -> None:
    from pa_agent.gui.widgets.flow_bar import FlowBar

    flow_bar = FlowBar()
    qtbot.addWidget(flow_bar)
    flow_bar.set_step_status(0, "active")

    assert "#4A7EBB" in flow_bar._steps[0]._dot.styleSheet()
    flow_bar._pulse_active_steps()
    assert "#4A7EBB" in flow_bar._steps[0]._dot.styleSheet()
    flow_bar._pulse_active_steps()
    assert "#5B8CC9" in flow_bar._steps[0]._dot.styleSheet()
