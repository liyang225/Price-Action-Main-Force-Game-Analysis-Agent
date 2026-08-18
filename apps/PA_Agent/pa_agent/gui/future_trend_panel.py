"""FutureTrendPanel — 未来走势预期页.

Hosts two prediction modules:
  1. 下一根K线预期 (migrated from DecisionPanel)
  2. 下一个市场周期预期 (new, AI-generated next_cycle_prediction)
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pa_agent.gui.text_format import bullet_point_lines
from pa_agent.gui.theme import tokens as T

from pa_agent.ai.cycle_enums import (
    CYCLE_ORDER,
    CYCLE_POSITION_ZH,
    format_cycle_with_direction,
)
from pa_agent.gui.prediction_format import (
    _PREDICTION_UNPREDICTABLE_COLOR,
    _PREDICTION_UNPREDICTABLE_LABEL,
    _format_prediction_probs_html,
)

_PANEL_BODY_SIZE = T.SIZE_BODY + 1
_PANEL_SECTION_SIZE = T.SIZE_SECTION + 1

_REASON_EDIT_CSS = (
    f"font-size: {_PANEL_BODY_SIZE}px; color: {T.FG}; line-height: 1.6;"
    "font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif;"
)

_SECTION_TITLE_CSS = (
    f"font-size: {_PANEL_SECTION_SIZE}px; font-weight: {T.WEIGHT_SEMIBOLD}; color: {T.FG_2};"
)

_DIRECTION_ZH: dict[str, str] = {
    "bullish": "看涨",
    "bearish": "看跌",
    "neutral": "中性",
}

_CHIP_BASE_CSS = (
    "font-size: 14px; font-weight: 600; padding: 10px 12px;"
    f"background-color: {T.SURFACE_2}; border: 1px solid {T.BORDER_SOFT};"
    f"border-radius: {T.RADIUS}px;"
)


def _chip_style(color: str) -> str:
    return f"{_CHIP_BASE_CSS} color: {color};"


class FutureTrendPanel(QWidget):
    """Renders next-bar and next-cycle prediction modules."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("未来走势预期")
        title.setObjectName("toolbarTitle")
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # ── Module 1: 下一根K线预期 ───────────────────────────────────────────
        self._bar_group = QFrame()
        self._bar_group.setObjectName("predictionGroup")
        bar_layout = QVBoxLayout(self._bar_group)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(6)

        self._bar_title = QLabel("下一根K线预期")
        self._bar_title.setStyleSheet(_SECTION_TITLE_CSS)
        bar_layout.addWidget(self._bar_title)

        self._bar_direction_label = QLabel("—")
        self._bar_direction_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bar_direction_label.setWordWrap(True)
        self._bar_direction_label.setStyleSheet(
            f"font-size: 15px; font-weight: {T.WEIGHT_SEMIBOLD}; padding: 8px;"
            f"background-color: {T.SURFACE_2}; border: 1px solid {T.BORDER_SOFT};"
            f"border-radius: {T.RADIUS}px; color: {T.FG_3};"
        )
        bar_layout.addWidget(self._bar_direction_label)

        self._bar_reasoning_edit = QTextEdit()
        self._bar_reasoning_edit.setReadOnly(True)
        self._bar_reasoning_edit.setObjectName("answerPane")
        self._bar_reasoning_edit.setStyleSheet(_REASON_EDIT_CSS)
        self._bar_reasoning_edit.setMinimumHeight(80)
        self._bar_reasoning_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        bar_layout.addWidget(self._bar_reasoning_edit, stretch=1)

        self._bar_group.setVisible(False)
        layout.addWidget(self._bar_group, stretch=1)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep2)

        # ── Module 2: 下一个市场周期预期 ─────────────────────────────────────
        self._cycle_group = QFrame()
        self._cycle_group.setObjectName("cyclePredictionGroup")
        cycle_layout = QVBoxLayout(self._cycle_group)
        cycle_layout.setContentsMargins(0, 0, 0, 0)
        cycle_layout.setSpacing(6)

        self._cycle_title = QLabel("下一个市场周期预期")
        self._cycle_title.setStyleSheet(_SECTION_TITLE_CSS)
        cycle_layout.addWidget(self._cycle_title)

        # 3 chips side by side (top-3 cycles by probability)
        self._top3_row = QWidget()
        top3_layout = QHBoxLayout(self._top3_row)
        top3_layout.setContentsMargins(0, 0, 0, 0)
        top3_layout.setSpacing(8)

        self._chip_labels: list[QLabel] = []
        for _ in range(3):
            lbl = QLabel("—")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            lbl.setStyleSheet(_chip_style(T.FG_2))
            top3_layout.addWidget(lbl, stretch=1)
            self._chip_labels.append(lbl)

        cycle_layout.addWidget(self._top3_row)

        self._cycle_direction_label = QLabel("—")
        self._cycle_direction_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cycle_direction_label.setStyleSheet(
            f"font-size: 14px; font-weight: {T.WEIGHT_SEMIBOLD}; color: {T.FG_2};"
        )
        cycle_layout.addWidget(self._cycle_direction_label)

        # Remaining 5 cycles label
        self._cycle_probs_label = QLabel("—")
        self._cycle_probs_label.setWordWrap(True)
        self._cycle_probs_label.setStyleSheet(
            f"font-size: 14px; color: {T.FG}; padding: 6px;"
            f"background-color: {T.SURFACE_2}; border: 1px solid {T.BORDER_SOFT};"
            f"border-radius: {T.RADIUS}px;"
        )
        cycle_layout.addWidget(self._cycle_probs_label)

        self._cycle_reasoning_edit = QTextEdit()
        self._cycle_reasoning_edit.setReadOnly(True)
        self._cycle_reasoning_edit.setObjectName("answerPane")
        self._cycle_reasoning_edit.setStyleSheet(_REASON_EDIT_CSS)
        self._cycle_reasoning_edit.setMinimumHeight(100)
        self._cycle_reasoning_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        cycle_layout.addWidget(self._cycle_reasoning_edit, stretch=1)

        self._cycle_group.setVisible(False)
        layout.addWidget(self._cycle_group, stretch=2)

    # ── Module 1: next_bar_prediction ────────────────────────────────────────

    def _apply_next_bar_prediction(self, decision: dict) -> None:
        """Render 下一根K线预期 module. Hides on missing/invalid data."""
        pred = decision.get("next_bar_prediction")
        if not isinstance(pred, dict):
            self._bar_group.setVisible(False)
            self._bar_direction_label.setText("—")
            self._bar_reasoning_edit.clear()
            return

        unpredictable = bool(pred.get("unpredictable", False))
        if unpredictable:
            line = _PREDICTION_UNPREDICTABLE_LABEL
            color = _PREDICTION_UNPREDICTABLE_COLOR
        else:
            probs = pred.get("probabilities")
            if isinstance(probs, dict):
                line = _format_prediction_probs_html(probs)
                color = T.FG
            else:
                line = "—"
                color = _PREDICTION_UNPREDICTABLE_COLOR

        self._bar_direction_label.setTextFormat(Qt.TextFormat.RichText)
        self._bar_direction_label.setText(line)
        self._bar_direction_label.setStyleSheet(
            f"font-size: 15px; font-weight: {T.WEIGHT_SEMIBOLD}; padding: 8px;"
            f"background-color: {T.SURFACE_2}; border: 1px solid {T.BORDER_SOFT};"
            f"border-radius: {T.RADIUS}px; color: {color};"
        )

        reasoning = str(pred.get("reasoning", "")).strip()
        if "程序根据阶段二诊断摘要补全" in reasoning or "程序参考分布" in reasoning:
            prefix = "【程序补全】模型未输出 next_bar_prediction，以下为参考预测。\n\n"
            if not reasoning.startswith("【程序补全】"):
                reasoning = prefix + reasoning
        self._bar_reasoning_edit.setPlainText(reasoning)
        self._bar_group.setVisible(True)

    # ── Module 2: next_cycle_prediction ──────────────────────────────────────

    def _reset_chips(self) -> None:
        for lbl in self._chip_labels:
            lbl.setText("—")
            lbl.setStyleSheet(_chip_style(T.FG_2))

    def _apply_next_cycle_prediction(self, decision: dict) -> None:
        """Render 下一个市场周期预期 module. Hides on missing/invalid data."""
        pred = decision.get("next_cycle_prediction")
        if not isinstance(pred, dict):
            self._cycle_group.setVisible(False)
            self._reset_chips()
            self._cycle_direction_label.setText("—")
            self._cycle_probs_label.setText("—")
            self._cycle_reasoning_edit.clear()
            return

        unpredictable = bool(pred.get("unpredictable", False))
        if unpredictable:
            self._reset_chips()
            self._chip_labels[0].setText(_PREDICTION_UNPREDICTABLE_LABEL)
            self._chip_labels[0].setStyleSheet(_chip_style(_PREDICTION_UNPREDICTABLE_COLOR))
            self._chip_labels[1].setVisible(False)
            self._chip_labels[2].setVisible(False)
            self._cycle_direction_label.setVisible(False)
            self._cycle_probs_label.setVisible(False)
            reasoning = str(pred.get("reasoning", "")).strip()
            self._set_structured_reasoning(self._cycle_reasoning_edit, reasoning)
            self._cycle_group.setVisible(True)
            return

        # Restore chip visibility
        for lbl in self._chip_labels:
            lbl.setVisible(True)

        direction = pred.get("direction")
        dir_key = str(direction or "").strip().lower()
        if dir_key == "bullish":
            cycle_color = T.MKT_UP
        elif dir_key == "bearish":
            cycle_color = T.MKT_DOWN
        else:
            cycle_color = T.FG_2

        direction_zh = _DIRECTION_ZH.get(dir_key, str(direction or "—"))

        # Sort all 8 cycles by probability descending
        probs = pred.get("probabilities")
        sorted_probs: list[tuple[str, int]] = []
        if isinstance(probs, dict):
            for key in CYCLE_ORDER:
                try:
                    pct = int(probs.get(key, 0) or 0)
                except (TypeError, ValueError):
                    pct = 0
                sorted_probs.append((key, pct))
            sorted_probs.sort(key=lambda x: x[1], reverse=True)

        # ── Top-3 chips, side by side ──
        top3 = sorted_probs[:3] if sorted_probs else []
        for i, lbl in enumerate(self._chip_labels):
            if i < len(top3):
                key, pct = top3[i]
                zh = format_cycle_with_direction(key, direction)
                role = "主路径 (概率最高)" if i == 0 else f"备选路径 {chr(64 + i)}"
                lbl.setText(
                    f"<div style='color:{T.FG}'>{zh}</div>"
                    f"<div style='font-size:17px;font-weight:600;color:{T.ACCENT if i == 0 else T.FG};margin-top:4px'>{pct}%</div>"
                    f"<div style='font-size:12px;color:{T.FG_2};margin-top:2px'>{role}</div>"
                )
                lbl.setStyleSheet(_chip_style(T.FG_2))
            else:
                lbl.setText("—")
                lbl.setStyleSheet(_chip_style(T.FG_2))

        arrow = "▲" if dir_key == "bullish" else "▼" if dir_key == "bearish" else "•"
        self._cycle_direction_label.setText(f"方向：{direction_zh} {arrow}")
        self._cycle_direction_label.setStyleSheet(
            f"font-size: 14px; font-weight: {T.WEIGHT_SEMIBOLD}; color: {cycle_color};"
        )
        self._cycle_direction_label.setVisible(True)

        # ── Remaining 5, sorted by probability ──
        rest = sorted_probs[3:] if len(sorted_probs) > 3 else []
        if rest:
            rest_parts = [f"{CYCLE_POSITION_ZH.get(k, k)}: {p}%" for k, p in rest]
            self._cycle_probs_label.setText("次要形态概率：  " + "   ·   ".join(rest_parts))
            self._cycle_probs_label.setVisible(True)
        else:
            self._cycle_probs_label.setVisible(False)

        reasoning = str(pred.get("reasoning", "")).strip()
        if "程序根据阶段二诊断摘要补全" in reasoning or "程序参考分布" in reasoning:
            prefix = "【程序补全】模型未输出 next_cycle_prediction，以下为参考预测。\n\n"
            if not reasoning.startswith("【程序补全】"):
                reasoning = prefix + reasoning
        self._set_structured_reasoning(self._cycle_reasoning_edit, reasoning)
        self._cycle_group.setVisible(True)

    @staticmethod
    def _set_structured_reasoning(editor: QTextEdit, reasoning: str) -> None:
        import html

        lines = bullet_point_lines(reasoning).splitlines()
        if not lines:
            editor.clear()
            return
        bullets = [f"<div>{html.escape(line)}</div>" for line in lines]
        editor.setHtml(
            f"<div style='color:{T.FG_2};font-weight:600;margin-bottom:4px'>推理推演逻辑</div>"
            "<div style='margin:0'>" + "".join(bullets) + "</div>"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def set_prediction(self, decision: dict) -> None:
        """Render both prediction modules from the decision dict."""
        self._apply_next_bar_prediction(decision)
        self._apply_next_cycle_prediction(decision)

    def clear(self) -> None:
        """Reset both modules to initial empty state and hide them."""
        self._bar_group.setVisible(False)
        self._bar_direction_label.setText("—")
        self._bar_direction_label.setStyleSheet(
            f"font-size: 15px; font-weight: {T.WEIGHT_SEMIBOLD}; padding: 8px;"
            f"background-color: {T.SURFACE_2}; border: 1px solid {T.BORDER_SOFT};"
            f"border-radius: {T.RADIUS}px; color: {T.FG_3};"
        )
        self._bar_reasoning_edit.clear()

        self._cycle_group.setVisible(False)
        self._reset_chips()
        for lbl in self._chip_labels:
            lbl.setVisible(True)
        self._cycle_direction_label.setText("—")
        self._cycle_direction_label.setVisible(True)
        self._cycle_probs_label.setText("—")
        self._cycle_probs_label.setVisible(True)
        self._cycle_reasoning_edit.clear()
