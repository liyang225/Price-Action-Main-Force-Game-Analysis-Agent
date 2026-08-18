"""DecisionPanel — trading decision + market diagnosis summary.

Visual rules (binding, see theme/tokens.py):
  * The trading conclusion is found by position, weight and a thin accent
    bar — never by enlarged, saturated "CTA" type.
  * Market direction follows the CN convention: 做多/涨 = red, 做空/跌 = green.
  * All colours come from theme.tokens; no hard-coded hex values here.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pa_agent.ai.cycle_enums import (
    format_cycle_position,
    format_cycle_with_direction,
    format_trend_label,
)
from pa_agent.gui.text_format import bullet_point_lines
from pa_agent.gui.theme import tokens as T
from pa_agent.util.trade_metrics import (
    compute_risk_reward,
    format_estimated_win_rate,
    max_risk_reward_ratio,
    min_risk_reward_ratio,
    passes_trader_equation,
)

_NO_ORDER = "不下单"
_PANEL_BODY_SIZE = T.SIZE_BODY + 1
_PANEL_SECTION_SIZE = T.SIZE_SECTION + 1
_PANEL_CONCLUSION_SIZE = T.SIZE_CONCLUSION + 1

# Reasoning body copy — quiet secondary reading
_REASON_FONT_CSS = (
    f"font-size: {_PANEL_BODY_SIZE}px; color: {T.FG_2}; line-height: 1.6;"
)
_REASON_EDIT_CSS = (
    f"font-size: {_PANEL_BODY_SIZE}px; color: {T.FG}; line-height: 1.6;"
    "font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif;"
)

# Section headers: one uniform, colourless style across the panel
_SECTION_TITLE_CSS = (
    f"font-size: {_PANEL_SECTION_SIZE}px; font-weight: {T.WEIGHT_SEMIBOLD}; color: {T.FG_2};"
)

_MARKET_PHASE_ZH: dict[str, str] = {
    "stable": "稳定",
    "transitioning": "过渡",
}


def _format_market_phase(raw: str) -> str:
    key = (raw or "").strip().lower()
    return _MARKET_PHASE_ZH.get(key, raw or "—")


def _direction_color(direction: object) -> str:
    """CN convention: 多/涨 → red, 空/跌 → green, otherwise neutral."""
    text = str(direction or "")
    if "多" in text:
        return T.MKT_UP
    if "空" in text:
        return T.MKT_DOWN
    return T.FG_2


def _trend_color(label: str) -> str:
    if label in ("上涨", "震荡偏多"):
        return T.MKT_UP
    if label in ("下跌", "震荡偏空"):
        return T.MKT_DOWN
    return T.FG


def _score_color(score: int) -> str:
    """Pass/fail semantics (not market direction): ok / watch / poor."""
    if score >= 70:
        return T.SUCCESS
    if score >= 50:
        return T.WARNING
    return T.DANGER


def _parse_score_100(value: object) -> int | None:
    """Parse 0–100 confidence score."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return max(0, min(100, int(value)))
    try:
        return max(0, min(100, int(float(str(value).strip()))))
    except (ValueError, TypeError):
        return None


def _price_item_html(caption: str, value: str) -> str:
    """Caption recedes, value carries the weight — both in mono for alignment."""
    return (
        f"<span style='color:{T.FG_3};'>{caption}</span>&nbsp;&nbsp;"
        f"<span style='color:{T.FG}; font-weight:{T.WEIGHT_MEDIUM};'>{value}</span>"
    )


class DecisionPanel(QWidget):
    """Renders market diagnosis + Stage-2 trading decision.

    The conclusion block keeps a constant position near the top of the
    panel; a 3px left accent bar carries the direction colour so the
    conclusion is located at a glance without shouting.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(12)

        title = QLabel("AI 交易决策")
        title.setObjectName("toolbarTitle")
        layout.addWidget(title)

        layout.addWidget(self._hairline())

        # ── 市场诊断 ──────────────────────────────────────────────────────
        diag_title = QLabel("市场诊断")
        diag_title.setStyleSheet(_SECTION_TITLE_CSS)
        layout.addWidget(diag_title)

        diag_row = QWidget()
        diag_row_layout = QHBoxLayout(diag_row)
        diag_row_layout.setContentsMargins(0, 0, 0, 0)
        diag_row_layout.setSpacing(8)

        self._trend_label = QLabel("趋势：—")
        self._cycle_label = QLabel("周期：—")
        self._phase_label = QLabel("阶段：—")
        for lbl in (self._trend_label, self._cycle_label, self._phase_label):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            lbl.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred,
            )
            self._apply_diag_summary_card_style(lbl, color=T.FG_3)
            diag_row_layout.addWidget(lbl, stretch=1)
        layout.addWidget(diag_row)

        diag_detail_row = QWidget()
        diag_detail_layout = QHBoxLayout(diag_detail_row)
        diag_detail_layout.setContentsMargins(0, 0, 0, 0)
        diag_detail_layout.setSpacing(8)
        self._next_cycle_label = QLabel("下一市场周期：—")
        self._support_label = QLabel("支撑（由近及远）：—")
        self._resistance_label = QLabel("阻力（由近及远）：—")
        for label in (
            self._next_cycle_label,
            self._support_label,
            self._resistance_label,
        ):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred,
            )
            label.setMinimumHeight(42)
            self._apply_context_chip_style(label)
            diag_detail_layout.addWidget(label, stretch=1)
        layout.addWidget(diag_detail_row)

        # ── 市场判断置信度（来自 Stage 2 diagnosis_confidence）───────────
        self._diag_conf_title = QLabel("市场判断置信度")
        self._diag_conf_title.setStyleSheet(_SECTION_TITLE_CSS + "margin-top: 6px;")
        layout.addWidget(self._diag_conf_title)

        self._diag_conf_bar = QProgressBar()
        self._diag_conf_bar.setRange(0, 100)
        self._diag_conf_bar.setTextVisible(False)
        self._diag_conf_bar.setFixedHeight(4)
        layout.addWidget(self._diag_conf_bar)

        self._diag_conf_label = QLabel("—")
        layout.addWidget(self._diag_conf_label)

        self._diag_reasoning_label = QLabel()
        self._diag_reasoning_label.setWordWrap(True)
        self._diag_reasoning_label.setStyleSheet(_REASON_FONT_CSS)
        layout.addWidget(self._diag_reasoning_label)

        layout.addWidget(self._hairline())

        # ── 交易决策 ──────────────────────────────────────────────────────
        trade_title = QLabel("交易决策")
        trade_title.setStyleSheet(_SECTION_TITLE_CSS)
        layout.addWidget(trade_title)

        # Conclusion block — constant position, thin direction accent bar.
        self._conclusion_bar = QFrame()
        self._conclusion_bar.setObjectName("conclusionBar")
        bar_layout = QVBoxLayout(self._conclusion_bar)
        bar_layout.setContentsMargins(12, 10, 12, 10)
        bar_layout.setSpacing(8)

        # Row 1 — the conclusion line: weight + position, not size or colour.
        summary_row = QWidget()
        summary_layout = QHBoxLayout(summary_row)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(10)

        self._conclusion_label = QLabel("—")
        self._conclusion_label.setStyleSheet(
            f"font-size: {_PANEL_CONCLUSION_SIZE}px; font-weight: {T.WEIGHT_SEMIBOLD};"
            f"color: {T.FG};"
        )

        self._direction_inline_label = QLabel()
        self._direction_inline_label.setStyleSheet(
            f"font-size: {_PANEL_BODY_SIZE}px; font-weight: {T.WEIGHT_MEDIUM};"
            f"color: {T.FG_2};"
        )

        self._trade_conf_inline_label = QLabel()
        self._trade_conf_inline_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        summary_layout.addWidget(self._conclusion_label)
        summary_layout.addWidget(self._direction_inline_label)
        summary_layout.addStretch(1)
        summary_layout.addWidget(self._trade_conf_inline_label)
        bar_layout.addWidget(summary_row)

        # Row 2 — decision price levels in mono, captions recede.
        self._trade_prices_row = QWidget()
        prices_layout = QHBoxLayout(self._trade_prices_row)
        prices_layout.setContentsMargins(0, 0, 0, 0)
        prices_layout.setSpacing(16)

        self._entry_label = QLabel()
        self._tp_label = QLabel()
        self._tp2_label = QLabel()
        self._sl_label = QLabel()
        for lbl in (self._entry_label, self._tp_label, self._tp2_label, self._sl_label):
            lbl.setStyleSheet(
                f"font-size: {_PANEL_BODY_SIZE}px; font-family: {T.FONT_MONO};"
            )
            prices_layout.addWidget(lbl, stretch=1)
        bar_layout.addWidget(self._trade_prices_row)

        # Row 3 — risk/reward + estimated win rate as quiet secondary metrics.
        metrics_row = QWidget()
        metrics_layout = QHBoxLayout(metrics_row)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setSpacing(8)

        self._rr_inline_label = QLabel("—")
        self._rr_inline_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._win_rate_inline_label = QLabel("—")
        self._win_rate_inline_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._win_rate_inline_label.setStyleSheet(
            f"font-size: {_PANEL_BODY_SIZE}px; color: {T.FG_2};"
        )
        metrics_layout.addWidget(self._rr_inline_label, stretch=1)
        metrics_layout.addWidget(self._win_rate_inline_label, stretch=1)
        self._metrics_row = metrics_row
        bar_layout.addWidget(metrics_row)

        layout.addWidget(self._conclusion_bar)

        self._trade_reasoning_label = QLabel()
        self._trade_reasoning_label.setWordWrap(True)
        self._trade_reasoning_label.setStyleSheet(_REASON_FONT_CSS)
        layout.addWidget(self._trade_reasoning_label)

        reasoning_title = QLabel("分析理由")
        reasoning_title.setStyleSheet(_SECTION_TITLE_CSS + "margin-top: 6px;")
        layout.addWidget(reasoning_title)

        self._reasoning_edit = QTextEdit()
        self._reasoning_edit.setReadOnly(True)
        self._reasoning_edit.setObjectName("answerPane")
        self._reasoning_edit.setStyleSheet(_REASON_EDIT_CSS)
        self._reasoning_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._reasoning_edit.setMinimumHeight(120)
        layout.addWidget(self._reasoning_edit, stretch=1)

        self.clear()

    @staticmethod
    def _hairline() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setStyleSheet(f"color: {T.CHART_GRID};")
        return line

    def _apply_diag_summary_card_style(self, label: QLabel, *, color: str) -> None:
        label.setStyleSheet(
            f"font-size: {_PANEL_BODY_SIZE}px; color: {color}; padding: 7px 9px;"
            f"background-color: {T.SURFACE_2};"
            f"border: 1px solid {T.BORDER_SOFT}; border-radius: {T.RADIUS}px;"
        )

    def _apply_context_chip_style(self, label: QLabel) -> None:
        label.setStyleSheet(
            f"font-size: {_PANEL_BODY_SIZE}px; font-weight: {T.WEIGHT_REGULAR};"
            f"padding: 8px 10px; color: {T.FG};"
            f"background-color: {T.SURFACE_2};"
            f"border: 1px solid {T.BORDER_SOFT}; border-radius: {T.RADIUS}px;"
        )

    # ── Data binding helpers ──────────────────────────────────────────────

    def _apply_market_diagnosis(
        self,
        diagnosis_summary: dict | None,
        stage1_diagnosis: dict | None = None,
    ) -> None:
        """Fill trend / cycle / phase from stage2 summary, fallback to stage1."""
        src: dict = {}
        if diagnosis_summary:
            src.update(diagnosis_summary)
        if stage1_diagnosis:
            for k, v in stage1_diagnosis.items():
                src.setdefault(k, v)

        direction = str(src.get("direction", "") or "")
        cycle_position = str(src.get("cycle_position", "") or "")
        alt_cycle = src.get("alternative_cycle_position")
        market_phase = str(src.get("market_phase", "") or "")

        trend = format_trend_label(direction, cycle_position)
        self._trend_label.setText(f"趋势：{trend}")
        self._apply_diag_summary_card_style(self._trend_label, color=_trend_color(trend))

        cycle_zh = format_cycle_with_direction(cycle_position, direction)
        cycle_text = f"周期：{cycle_zh}"
        if alt_cycle:
            cycle_text += f"（备选 {format_cycle_position(str(alt_cycle))}）"
        self._cycle_label.setText(cycle_text)
        self._apply_diag_summary_card_style(self._cycle_label, color=T.FG)

        if market_phase:
            phase_zh = _format_market_phase(market_phase)
            extra = ""
            risk = src.get("transition_risk")
            if market_phase == "transitioning" and risk:
                extra = f" · 风险 {risk}"
            self._phase_label.setText(f"阶段：{phase_zh}{extra}")
            phase_color = T.WARNING if market_phase == "transitioning" else T.FG
            self._apply_diag_summary_card_style(self._phase_label, color=phase_color)
        else:
            self._phase_label.setText("阶段：—")
            self._apply_diag_summary_card_style(self._phase_label, color=T.FG)
        self._phase_label.setVisible(True)

    def _apply_diagnosis_confidence(
        self,
        diagnosis_confidence: object,
        diagnosis_confidence_reasoning: str | None,
    ) -> None:
        """Render market-judgment confidence bar (Stage 2 diagnosis_confidence)."""
        score = _parse_score_100(diagnosis_confidence)
        if score is not None:
            c_color = _score_color(score)
            self._diag_conf_bar.setValue(score)
            self._diag_conf_bar.setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {c_color}; }}"
            )
            self._diag_conf_label.setText(f"评分 {score} / 100")
            self._diag_conf_label.setStyleSheet(
                f"color: {c_color}; font-size: {_PANEL_BODY_SIZE}px;"
                f"font-weight: {T.WEIGHT_MEDIUM};"
            )
            reason_text = str(diagnosis_confidence_reasoning or "").strip()
            self._diag_reasoning_label.setText(
                f"理由：{reason_text}" if reason_text else ""
            )
            self._diag_conf_title.setVisible(True)
            self._diag_conf_bar.setVisible(True)
            self._diag_conf_label.setVisible(True)
            self._diag_reasoning_label.setVisible(bool(reason_text))
        else:
            self._diag_conf_bar.setValue(0)
            self._diag_conf_title.setVisible(False)
            self._diag_conf_bar.setVisible(False)
            self._diag_conf_label.setVisible(False)
            self._diag_reasoning_label.setVisible(False)

    @staticmethod
    def _format_levels(value: object) -> str:
        if isinstance(value, (list, tuple)):
            levels = [str(level).strip() for level in value if str(level).strip()]
            return " / ".join(levels) or "—"
        text = str(value or "").strip()
        return text or "—"

    def _apply_context_levels(
        self,
        decision: dict,
        diagnosis_summary: dict | None,
        stage1_diagnosis: dict | None,
    ) -> None:
        prediction = decision.get("next_cycle_prediction")
        next_cycle = "—"
        if isinstance(prediction, dict) and not prediction.get("unpredictable", False):
            cycle = str(prediction.get("cycle", "") or "")
            direction = prediction.get("direction")
            if cycle:
                next_cycle = format_cycle_with_direction(cycle, direction)

        stage1 = stage1_diagnosis or {}
        summary = diagnosis_summary or {}
        support_levels = stage1.get("support_levels") or summary.get("support_levels")
        resistance_levels = stage1.get("resistance_levels") or summary.get(
            "resistance_levels"
        )
        self._next_cycle_label.setText(f"下一市场周期：{next_cycle}")
        self._support_label.setText(
            f"支撑（由近及远）：{self._format_levels(support_levels)}"
        )
        self._resistance_label.setText(
            f"阻力（由近及远）：{self._format_levels(resistance_levels)}"
        )

    def _apply_trade_confidence_inline(
        self,
        trade_confidence: object,
        trade_confidence_reasoning: str | None,
        *,
        no_order: bool = False,
    ) -> None:
        """Show trade confidence on the conclusion line; optional reasoning below."""
        score = _parse_score_100(trade_confidence)
        if score is not None:
            c_color = _score_color(score)
            hint = "观望" if no_order else "入场"
            self._trade_conf_inline_label.setText(
                f"置信度 {score} / 100 · {hint}"
            )
            self._trade_conf_inline_label.setStyleSheet(
                f"font-size: {_PANEL_BODY_SIZE}px; font-weight: {T.WEIGHT_MEDIUM};"
                f"color: {c_color};"
            )
            self._trade_conf_inline_label.setVisible(True)
            reason_text = str(trade_confidence_reasoning or "").strip()
            self._trade_reasoning_label.setText(
                f"置信度理由：{reason_text}" if reason_text else ""
            )
            self._trade_reasoning_label.setVisible(bool(reason_text))
        else:
            self._trade_conf_inline_label.setText("")
            self._trade_conf_inline_label.setVisible(False)
            self._trade_reasoning_label.setVisible(False)

    def _set_conclusion_bar_style(self, accent: str | None = None) -> None:
        """3px left accent bar carries the direction; geometry stays constant."""
        bar_color = accent if accent else "transparent"
        self._conclusion_bar.setStyleSheet(
            "QFrame#conclusionBar {"
            f"  background-color: {T.SURFACE_2};"
            f"  border: 1px solid {T.BORDER_SOFT};"
            f"  border-left: 3px solid {bar_color};"
            f"  border-radius: {T.RADIUS}px;"
            "}"
        )

    def _reset_conclusion_bar_side_labels(self) -> None:
        self._rr_inline_label.setText("—")
        self._win_rate_inline_label.setText("—")
        self._rr_inline_label.setVisible(False)
        self._win_rate_inline_label.setVisible(False)
        self._metrics_row.setVisible(False)

    def _set_conclusion(self, text: str, *, color: str) -> None:
        self._conclusion_label.setText(text)
        self._conclusion_label.setStyleSheet(
            f"font-size: {_PANEL_CONCLUSION_SIZE}px; font-weight: {T.WEIGHT_SEMIBOLD};"
            f"color: {color};"
        )

    def _set_price_row(
        self,
        entry: Any = None,
        tp: Any = None,
        tp2: Any = None,
        sl: Any = None,
    ) -> None:
        self._entry_label.setText(
            _price_item_html("入场", f"{entry:.5g}" if entry is not None else "—")
        )
        self._tp_label.setText(
            _price_item_html("TP1", f"{tp:.5g}" if tp is not None else "—")
        )
        self._tp2_label.setText(
            _price_item_html("TP2", f"{tp2:.5g}" if tp2 is not None else "—")
        )
        self._sl_label.setText(
            _price_item_html("止损", f"{sl:.5g}" if sl is not None else "—")
        )

    # ── Public API ────────────────────────────────────────────────────────

    def set_decision(
        self,
        decision: dict,
        *,
        diagnosis_summary: dict | None = None,
        stage1_diagnosis: dict | None = None,
        decision_stance: str | None = None,
        confidence_threshold: int | None = None,
    ) -> None:
        self._apply_market_diagnosis(diagnosis_summary, stage1_diagnosis)
        self._apply_context_levels(decision, diagnosis_summary, stage1_diagnosis)

        order_type = decision.get("order_type", _NO_ORDER)
        reasoning = decision.get("reasoning", decision.get("brief_reasoning", ""))
        # Confidence gate: suppress order display when confidence < threshold
        if confidence_threshold is not None and confidence_threshold > 0 and order_type != _NO_ORDER:
            raw_conf = decision.get("trade_confidence")
            try:
                conf_val = int(float(str(raw_conf).strip())) if raw_conf is not None and raw_conf != "" else -1
            except (ValueError, TypeError):
                conf_val = -1
            if conf_val < confidence_threshold:
                order_type = _NO_ORDER
                prefix = "有入场机会，但置信度未通过"
                reasoning = f"{prefix}\n\n{reasoning}" if reasoning else prefix
        diag_conf = decision.get("diagnosis_confidence", None)
        diag_conf_reasoning = decision.get("diagnosis_confidence_reasoning", None)
        trade_conf = decision.get("trade_confidence", None)
        trade_conf_reasoning = decision.get("trade_confidence_reasoning", None)

        self._apply_diagnosis_confidence(diag_conf, diag_conf_reasoning)

        if order_type == _NO_ORDER:
            self._reset_conclusion_bar_side_labels()
            self._set_conclusion(_NO_ORDER, color=T.FG_2)
            self._direction_inline_label.setText("")
            self._direction_inline_label.setVisible(False)
            self._trade_prices_row.setVisible(False)
            self._set_conclusion_bar_style(accent=None)
            self._apply_trade_confidence_inline(
                trade_conf, trade_conf_reasoning,
                no_order=True,
            )
        else:
            direction = decision.get("order_direction", "—")
            entry = decision.get("entry_price")
            tp = decision.get("take_profit_price")
            tp2 = decision.get("take_profit_price_2")
            sl = decision.get("stop_loss_price")
            dir_color = _direction_color(direction)

            self._set_conclusion(str(order_type), color=T.FG)
            self._direction_inline_label.setText(f"方向 {direction}")
            self._direction_inline_label.setStyleSheet(
                f"font-size: {_PANEL_BODY_SIZE}px; font-weight: {T.WEIGHT_MEDIUM};"
                f"color: {dir_color};"
            )
            self._direction_inline_label.setVisible(True)

            self._set_price_row(entry, tp, tp2, sl)
            self._trade_prices_row.setVisible(True)

            self._set_conclusion_bar_style(accent=dir_color)

            rr = compute_risk_reward(entry, tp, sl, direction)
            if rr is not None:
                ratio = float(rr["ratio"])
                risk = float(rr["risk"])
                reward = float(rr["reward"])
                win_pct = _parse_score_100(decision.get("estimated_win_rate"))
                eq_ok = (
                    win_pct is not None
                    and passes_trader_equation(win_pct, risk, reward)
                )
                min_rr = min_risk_reward_ratio(decision_stance)
                max_rr = max_risk_reward_ratio()
                metrics_ok = (
                    ratio >= min_rr
                    and (max_rr is None or ratio <= max_rr)
                    and (eq_ok if win_pct is not None else True)
                )
                eq_note = ""
                if win_pct is not None:
                    eq_note = " · 方程通过" if eq_ok else " · 方程不通过"
                self._rr_inline_label.setText(
                    f"盈亏比 {rr['ratio_text']}（风险 {risk:.4g} / 回报 {reward:.4g}）{eq_note}"
                )
                rr_color = T.SUCCESS if metrics_ok else T.DANGER
                self._rr_inline_label.setStyleSheet(
                    f"color: {rr_color}; font-size: {_PANEL_BODY_SIZE}px;"
                    f"font-weight: {T.WEIGHT_MEDIUM};"
                )
            else:
                self._rr_inline_label.setText("盈亏比 —（三价无效）")
                self._rr_inline_label.setStyleSheet(
                    f"color: {T.DANGER}; font-size: {_PANEL_BODY_SIZE}px;"
                    f"font-weight: {T.WEIGHT_MEDIUM};"
                )
            self._rr_inline_label.setVisible(True)

            win_rate = format_estimated_win_rate(decision)
            self._win_rate_inline_label.setText(
                f"预估胜率 {win_rate}" if win_rate else "预估胜率 —"
            )
            self._win_rate_inline_label.setVisible(True)
            self._metrics_row.setVisible(True)

            self._apply_trade_confidence_inline(
                trade_conf, trade_conf_reasoning,
                no_order=False,
            )

        self._reasoning_edit.setPlainText(bullet_point_lines(reasoning))

    def clear(self) -> None:
        self._trend_label.setText("趋势：—")
        self._apply_diag_summary_card_style(self._trend_label, color=T.FG)
        self._cycle_label.setText("周期：—")
        self._apply_diag_summary_card_style(self._cycle_label, color=T.FG_3)
        self._phase_label.setText("阶段：—")
        self._apply_diag_summary_card_style(self._phase_label, color=T.FG)
        self._phase_label.setVisible(True)
        self._next_cycle_label.setText("下一市场周期：—")
        self._support_label.setText("支撑（由近及远）：—")
        self._resistance_label.setText("阻力（由近及远）：—")

        self._diag_conf_bar.setValue(0)
        self._diag_conf_title.setVisible(False)
        self._diag_conf_bar.setVisible(False)
        self._diag_conf_label.setVisible(False)
        self._diag_reasoning_label.setVisible(False)

        self._reset_conclusion_bar_side_labels()
        self._set_conclusion("等待分析", color=T.FG_3)
        self._direction_inline_label.setText("")
        self._direction_inline_label.setVisible(False)
        self._trade_prices_row.setVisible(False)
        self._trade_conf_inline_label.setVisible(False)
        self._trade_reasoning_label.setVisible(False)
        self._set_conclusion_bar_style(accent=None)

        self._reasoning_edit.clear()
