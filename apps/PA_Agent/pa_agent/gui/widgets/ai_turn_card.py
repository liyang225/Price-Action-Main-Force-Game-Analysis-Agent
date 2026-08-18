"""Unified AI turn display — tabbed panes, single scroll per view."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def _mono_edit() -> QTextEdit:
    edit = QTextEdit()
    edit.setReadOnly(True)
    edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
    edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    edit.setMinimumHeight(120)
    return edit


class AITurnCard(QFrame):
    """One AI turn: Prompt / Reasoning / Answer in tabs (one scrollbar at a time)."""

    def __init__(
        self,
        title: str,
        *,
        system_prompt: str = "",
        user_prompt: str = "",
        show_prompt: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("aiTurnCard")
        self._auto_scroll = True
        self._streaming = False
        self._content_started = False
        self._system_prompt = system_prompt
        self._user_prompt = user_prompt

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        header_row = QHBoxLayout()
        self._header = QLabel(title)
        self._header.setObjectName("stageHeader")
        header_row.addWidget(self._header, stretch=1)
        self._status = QLabel("")
        self._status.setObjectName("mutedLabel")
        header_row.addWidget(self._status)

        auto_scroll_cb = QCheckBox("自动滚动")
        auto_scroll_cb.setChecked(True)
        auto_scroll_cb.toggled.connect(self._set_auto_scroll)
        header_row.addWidget(auto_scroll_cb)

        btn_copy_r = QPushButton("复制推理")
        btn_copy_r.setFixedWidth(68)
        btn_copy_r.clicked.connect(self._copy_reasoning)
        header_row.addWidget(btn_copy_r)

        btn_copy_a = QPushButton("复制回答")
        btn_copy_a.setFixedWidth(68)
        btn_copy_a.clicked.connect(self._copy_answer)
        header_row.addWidget(btn_copy_a)

        outer.addLayout(header_row)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        self._reasoning_edit = _mono_edit()
        self._reasoning_edit.setObjectName("reasoningPane")
        reasoning_page = QWidget()
        rp_layout = QVBoxLayout(reasoning_page)
        rp_layout.setContentsMargins(0, 4, 0, 0)
        rp_layout.addWidget(self._reasoning_edit)
        self._tabs.addTab(reasoning_page, "推理")

        self._answer_edit = _mono_edit()
        self._answer_edit.setObjectName("answerPane")
        answer_page = QWidget()
        ap_layout = QVBoxLayout(answer_page)
        ap_layout.setContentsMargins(0, 4, 0, 0)
        ap_layout.addWidget(self._answer_edit)
        self._tabs.addTab(answer_page, "回答")

        if show_prompt and (system_prompt or user_prompt):
            self._tabs.addTab(
                self._build_prompt_page(system_prompt, user_prompt),
                "Prompt",
            )

        self._tabs.setCurrentIndex(0)
        outer.addWidget(self._tabs, stretch=1)

    def _build_prompt_page(self, system: str, user: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        if system:
            sys_toggle = QToolButton()
            sys_toggle.setCheckable(True)
            sys_toggle.setChecked(False)
            sys_toggle.setText("▶ System（点击展开）")
            sys_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            sys_edit = _mono_edit()
            sys_edit.setObjectName("answerPane")
            sys_edit.setPlainText(system)
            sys_edit.setVisible(False)
            sys_toggle.toggled.connect(sys_edit.setVisible)
            sys_toggle.toggled.connect(
                lambda c, b=sys_toggle: b.setText(
                    "▼ System" if c else "▶ System（点击展开）"
                )
            )
            layout.addWidget(sys_toggle)
            layout.addWidget(sys_edit)

        user_edit = _mono_edit()
        user_edit.setObjectName("answerPane")
        user_edit.setPlainText(user)
        layout.addWidget(user_edit, stretch=1)
        return page

    def _set_auto_scroll(self, enabled: bool) -> None:
        self._auto_scroll = enabled

    def set_active_tab(self, tab: str) -> None:
        mapping = {"reasoning": 0, "answer": 1, "prompt": 2}
        idx = mapping.get(tab, 0)
        if idx < self._tabs.count():
            self._tabs.setCurrentIndex(idx)

    def set_streaming(self, streaming: bool) -> None:
        self._streaming = streaming
        self._content_started = False
        self._status.setText("生成中…" if streaming else "")
        if streaming:
            self.set_active_tab("reasoning")

    def append_reasoning(self, chunk: str) -> None:
        if self._streaming and self._tabs.currentIndex() != 0:
            self.set_active_tab("reasoning")
        self._append_to(self._reasoning_edit, chunk)

    def append_content(self, chunk: str) -> None:
        if self._streaming and not self._content_started:
            self._content_started = True
            self.set_active_tab("answer")
        self._append_to(self._answer_edit, chunk)
        if self._streaming:
            self._status.setText("输出中…")

    def set_reasoning(self, text: str) -> None:
        self._reasoning_edit.setPlainText(text)

    def set_content(self, text: str) -> None:
        self._answer_edit.setPlainText(text)

    def mark_done(self, elapsed_s: float | None = None) -> None:
        self._streaming = False
        self._status.setText(
            f"完成 · {elapsed_s:.1f}s" if elapsed_s is not None else "完成"
        )

    def scroll_active_to_bottom(self) -> None:
        idx = self._tabs.currentIndex()
        if idx == 0:
            self._scroll_edit(self._reasoning_edit)
        elif idx == 1:
            self._scroll_edit(self._answer_edit)

    def _append_to(self, edit: QTextEdit, chunk: str) -> None:
        cursor = edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(chunk)
        edit.setTextCursor(cursor)
        self._scroll_edit(edit)

    def _scroll_edit(self, edit: QTextEdit) -> None:
        if not self._auto_scroll:
            return
        sb = edit.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _copy_reasoning(self) -> None:
        QApplication.clipboard().setText(self._reasoning_edit.toPlainText())

    def _copy_answer(self) -> None:
        QApplication.clipboard().setText(self._answer_edit.toPlainText())


class ChatBubble(QFrame):
    """Compact user message."""

    def __init__(
        self,
        role: str,
        content: str,
        reasoning: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("aiTurnCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        role_lbl = QLabel("用户")
        role_lbl.setStyleSheet("font-weight: bold; color: #4A7EBB;")
        layout.addWidget(role_lbl)

        edit = _mono_edit()
        edit.setPlainText(content)
        edit.setObjectName("answerPane")
        layout.addWidget(edit, stretch=1)
