from __future__ import annotations


def test_decision_tree_tables_show_column_separators(qtbot) -> None:
    from pa_agent.gui.decision_tree_panel import DecisionTreePanel

    panel = DecisionTreePanel()
    qtbot.addWidget(panel)

    assert panel._path_table.showGrid()
    assert "border-right: 1px solid #333A45" in panel._path_table.styleSheet()
    assert "border-right: 1px solid #333A45" in panel._tree.styleSheet()


def test_full_decision_tree_question_column_defaults_to_300px(qtbot) -> None:
    from pa_agent.gui.decision_tree_panel import DecisionTreePanel

    panel = DecisionTreePanel()
    qtbot.addWidget(panel)

    assert panel._tree.columnWidth(1) == 300
