from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QComboBox, QFrame


def test_combo_popup_uses_dark_frameless_container(qtbot, qapp) -> None:
    from pa_agent.gui.theme.apply import apply_theme

    apply_theme(qapp)
    combo = QComboBox()
    combo.addItems(["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"])
    qtbot.addWidget(combo)
    combo.show()
    combo.showPopup()
    qapp.processEvents()

    view = combo.view()
    container = view.window()
    assert container.palette().color(container.backgroundRole()) == QColor("#181C22")
    assert view.frameShape() == QFrame.Shape.NoFrame
    assert "border: none" in view.styleSheet()
    assert "QAbstractItemView::item:selected" in view.styleSheet()
    highlight = view.palette().color(QPalette.ColorRole.Highlight)
    assert (highlight.red(), highlight.green(), highlight.blue(), highlight.alpha()) == (
        74,
        126,
        187,
        38,
    )
    assert view.palette().color(QPalette.ColorRole.HighlightedText) == QColor("#5B8CC9")
    assert type(view.itemDelegate()).__name__ == "_ComboPopupItemDelegate"
    assert container.graphicsEffect() is not None


def test_combo_popup_hover_uses_neutral_preview_color(qtbot, qapp) -> None:
    from pa_agent.gui.theme.apply import apply_theme

    apply_theme(qapp)
    combo = QComboBox()
    combo.addItems(["1m", "5m", "15m", "30m"])
    combo.setCurrentIndex(2)
    combo.resize(160, 34)
    qtbot.addWidget(combo)
    combo.show()
    combo.showPopup()
    qapp.processEvents()

    view = combo.view()
    hover_rect = view.visualRect(view.model().index(1, 0))
    QTest.mouseMove(view.viewport(), hover_rect.center())
    qapp.processEvents()
    image = view.viewport().grab().toImage()
    sampled = image.pixelColor(image.width() - 4, hover_rect.center().y())
    assert sampled.name().upper() in {"#22272F", "#181C22"}
    selected_rect = view.visualRect(view.model().index(2, 0))
    selected_fill = image.pixelColor(image.width() - 4, selected_rect.center().y())
    selected_accent = image.pixelColor(1, selected_rect.center().y())
    assert selected_fill.name().upper() in {"#1F2B39", "#22272F", "#181C22"}
    assert selected_accent.name().upper() in {"#4A7EBB", "#22272F", "#181C22"}


def test_combo_popup_sizes_for_styled_rows_without_scroll_arrows(qtbot, qapp) -> None:
    from pa_agent.gui.theme.apply import apply_theme

    apply_theme(qapp)
    combo = QComboBox()
    combo.addItems(["1m", "15m", "1h"])
    combo.resize(160, 34)
    qtbot.addWidget(combo)
    combo.show()
    qapp.processEvents()

    view = combo.view()
    expected_rows_height = sum(view.sizeHintForRow(row) for row in range(combo.count()))
    combo.showPopup()
    qapp.processEvents()

    assert view.viewport().height() >= expected_rows_height
