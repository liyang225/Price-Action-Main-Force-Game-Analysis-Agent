"""Human-readable labels for validation error prefixes (P0-3)."""
from __future__ import annotations

_PREFIX_RULES: tuple[tuple[str, str], ...] = (
    ("gate:", "【闸门】"),
    ("gate_trace", "【闸门路径】"),
    ("coherence:", "【一致性】"),
    ("s1:", "【阶段一】"),
    ("s2:", "【阶段二】"),
    ("trace:", "【决策路径】"),
    ("metrics:", "【盈亏比/方程】"),
    ("limit long:", "【限价做多·K1】"),
    ("limit short:", "【限价做空·K1】"),
    ("breakout_price:", "【突破价】"),
    ("signal_chain:", "【信号链】"),
    ("next_bar_prediction", "【下一根预期】"),
    ("bar_by_bar", "【逐棒摘要】"),
    ("diagnosis_summary", "【诊断摘要】"),
    ("order_direction", "【下单方向】"),
    ("always_in", "【AIL状态】"),
    ("terminal.outcome", "【终局结果】"),
    ("terminal", "【终局】"),
    ("placing an order", "【下单规则】"),
    ("incremental", "【增量分析】"),
    ("provider:quota_exhausted", "【API 额度】"),
)


def format_validation_errors(
    invalid_fields: list[str],
    *,
    missing_fields: list[str] | None = None,
    max_items: int = 8,
) -> str:
    """Build a short Chinese summary for status bar / exception message."""
    lines: list[str] = []
    if missing_fields:
        lines.append("缺少字段: " + ", ".join(missing_fields[:max_items]))
    for raw in invalid_fields[:max_items]:
        lines.append(_label_one(raw))
    extra = len(invalid_fields) - max_items
    if extra > 0:
        lines.append(f"…另有 {extra} 条")
    return "；".join(lines) if lines else ""


def _label_one(raw: str) -> str:
    text = str(raw).strip()
    for prefix, label in _PREFIX_RULES:
        if text.startswith(prefix) or prefix in text:
            body = text.split(":", 1)[-1].strip() if ":" in text else text
            return f"{label}{body}"
    return text
