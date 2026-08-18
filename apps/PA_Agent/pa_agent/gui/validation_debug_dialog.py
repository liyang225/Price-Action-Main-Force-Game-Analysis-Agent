"""Modal dialog for validation / analysis failures (debug-friendly)."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def show_validation_debug_dialog(
    parent: QWidget | None,
    *,
    title: str,
    summary: str,
    body: str,
) -> None:
    """Show scrollable debug text with copy-to-clipboard."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(760, 520)

    layout = QVBoxLayout(dlg)
    if summary.strip():
        summary_label = QLabel(summary)
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)

    edit = QTextEdit()
    edit.setReadOnly(True)
    edit.setPlainText(body)
    layout.addWidget(edit)

    row = QHBoxLayout()
    btn_copy = QPushButton("复制全部")
    btn_close = QPushButton("关闭")

    def _copy() -> None:
        QApplication.clipboard().setText(body)

    btn_copy.clicked.connect(_copy)
    btn_close.clicked.connect(dlg.accept)
    row.addWidget(btn_copy)
    row.addStretch()
    row.addWidget(btn_close)
    layout.addLayout(row)

    dlg.exec()
