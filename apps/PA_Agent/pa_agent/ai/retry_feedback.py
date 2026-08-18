"""Build structured retry user messages from ValidationError."""
from __future__ import annotations

import json
from typing import Any, Literal

from pa_agent.ai.retry_policy import StageName, extract_feedback_targets
from pa_agent.ai.validation_messages import format_validation_errors

StageLit = Literal["stage1", "stage2"]

_CATEGORY_ZH: dict[str, str] = {
    "a": "JSON 语法错误",
    "b": "缺少必填字段",
    "c": "字段值/一致性不符合规则",
    "d": "未输出 JSON（正文为空或纯文字）",
}

_FORBIDDEN_STAGE1 = (
    "direction / cycle_position / gate_result（除非反馈明确要求修改且你有 K 线依据；"
    "增量更新时若新 K 线改变结构，可在 incremental_delta.changed_fields 中列出 direction/"
    "cycle_position，并用 node_overrides §2.3 说明依据）",
    "bar_by_bar_summary[].bar_type（必须服从程序几何表）",
    "程序锁定节点 §1.1",
)

_FORBIDDEN_STAGE2 = (
    "diagnosis_summary.cycle_position / direction（除非反馈明确要求）",
    "为通过校验把 order_type 从「不下单」改成下单（或反之）",
    "交易者方程 10.3 的数值结论（须基于真实 entry/stop/target 重算）",
)


def _try_parse_obj(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _geometry_excerpt(frame: Any, limit: int = 8) -> str:
    try:
        from pa_agent.ai.kline_features import compute_kline_geometry_features

        feats = compute_kline_geometry_features(frame, limit=limit)
        lines = ["程序 K 线几何表（bar_type 权威来源，bar_by_bar_summary 必须一致）："]
        for f in feats:
            lines.append(f"  K{f.seq}: {f.bar_type}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""


def build_retry_feedback(
    err: Any,
    *,
    stage: StageLit,
    attempt: int,
    max_attempts: int,
    frame: Any = None,
    previous_raw: str | None = None,
) -> str:
    """Compose a concise retry user turn."""
    category = str(getattr(err, "category", "c") or "c")
    missing = list(getattr(err, "missing_fields", None) or [])
    invalid = list(getattr(err, "invalid_fields", None) or [])
    detail = format_validation_errors(invalid, missing_fields=missing, max_items=6)
    cat_zh = _CATEGORY_ZH.get(category, category)

    lines = [
        f"## 校验未通过（第 {attempt}/{max_attempts} 次重试）",
        "",
        f"阶段：**{stage}**",
        f"失败类型：**{cat_zh}** (category={category})",
        f"说明：{getattr(err, 'message', '')}",
    ]
    if getattr(err, "parse_position", None):
        lines.append(f"JSON 解析位置：{err.parse_position}")
    lines.append("")
    lines.append("**必须修正（仅修下列项；其余字段保持与上一轮一致）：**")
    if missing:
        for i, m in enumerate(missing[:6], 1):
            lines.append(f"{i}. [缺少] {m}")
    for i, inv in enumerate(invalid[:6], start=len(missing[:6]) + 1):
        lines.append(f"{i}. [无效] {inv}")
    if detail:
        lines.append(f"摘要：{detail}")

    lines.append("")
    lines.append("**禁止为通过校验而修改：**")
    forbidden = _FORBIDDEN_STAGE1 if stage == "stage1" else _FORBIDDEN_STAGE2
    for item in forbidden:
        lines.append(f"- {item}")

    geo = _geometry_excerpt(frame) if stage == "stage1" and frame is not None else ""
    if geo:
        lines.append("")
        lines.append(geo)

    if stage == "stage1" and (
        any("gate_trace" in inv and "answer" in inv for inv in invalid)
        or "冲突" in getattr(err, "message", "")
    ):
        lines.append("")
        lines.append("**gate_trace answer 枚举提示：**")
        lines.append(
            "- §2.2：`answer` 只能用 **是/否/中性/等待/不适用**；"
            "「同向/冲突/背景中性」写在 `branch`（如 aligned / conflict / neutral_background），"
            "**禁止**把「冲突」写在 answer。"
        )

    if stage == "stage1" and any("node_overrides" in inv and "answer" in inv for inv in invalid):
        lines.append("")
        lines.append("**node_overrides answer 枚举提示：**")
        lines.append(
            "- `answer` 只能用 **是/否/中性/等待/不适用**（与 gate_trace 相同）；"
            "**禁止**写「多头/空头/bullish/bearish」等方向词。"
            "方向信息写在 `branch`（如 bullish / bearish / neutral）。"
        )

    if stage == "stage1" and any(
        "bar_by_bar_summary" in inv and "role" in inv for inv in invalid
    ):
        lines.append("")
        lines.append("**bar_by_bar_summary.role 枚举提示：**")
        lines.append(
            "- `role` 只能用 **structure / signal / entry / confirmation / noise / trap / climax / test**；"
            "**禁止**写 support/resistance/continuation 或 detected_patterns 中的形态名。"
        )

    if stage == "stage1" and any(
        "bar_by_bar_summary" in inv and "trapped_side" in inv for inv in invalid
    ):
        lines.append("")
        lines.append("**bar_by_bar_summary.trapped_side 枚举提示：**")
        lines.append(
            "- `trapped_side` 只能用 **bulls / bears / both / none / unknown**；"
            "**禁止** null/空值；无明确被套方向时写 **none**。"
        )

    if stage == "stage1" and any(
        "bar_by_bar_summary" in inv and "context_effect" in inv for inv in invalid
    ):
        lines.append("")
        lines.append("**bar_by_bar_summary.context_effect 枚举提示：**")
        lines.append(
            "- `context_effect` 只能用 **strengthens_bull / weakens_bull / "
            "strengthens_bear / weakens_bear / neutral / transition**；"
            "**禁止** strengthens_bash 等拼写错误。"
        )

    if stage == "stage2" and any("decision.reasoning" in inv for inv in invalid):
        lines.append("")
        lines.append("**decision.reasoning 字数提示：**")
        lines.append(
            "- `decision.reasoning` **≤280 字**（中文）；只写结论 + 1–2 个关键依据，"
            "细节放在 decision_trace §10.3 或 key_factors。"
        )

    if stage == "stage2" and any(
        "decision_trace" in inv and "answer" in inv for inv in invalid
    ):
        lines.append("")
        lines.append("**decision_trace answer 枚举提示：**")
        lines.append(
            "- `answer` 只能用 **是/否/中性/等待/不适用**；"
            "「部分一致」写 **中性**，「尚需下一根K线确认」写 **等待**；"
            "**禁止**写「是部分」「部分」「待确认」等。"
        )

    if category == "a":
        lines.append("")
        lines.append("**JSON 语法提示：**")
        lines.append(
            "- `content` 必须是**裸 JSON 对象**（以 `{` 开头、以 `}` 结尾），"
            "**禁止** ` ```json ` 代码围栏或前后缀说明文字。"
        )
        prev = (previous_raw or "").strip()
        if "```" in prev:
            lines.append(
                "- 上一轮使用了 markdown 围栏或非 JSON 前缀；请删掉围栏与说明，"
                "只输出可 `json.loads` 的完整 JSON。"
            )

    lines.append("")
    lines.append(
        "请根据以上说明，在 assistant 正文 `content` 输出**完整**阶段"
        f"{'一' if stage == 'stage1' else '二'}裸 JSON（不要 markdown 围栏）。"
        "交易结论须与 K 线分析一致，不得仅为修字段而反转方向。"
    )

    if category == "d":
        if not (previous_raw or "").strip():
            lines.append(
                "⚠️ 上一轮正文 content 为空：请把 JSON 写在 content，不要只写在思考区。"
            )
        if stage == "stage2":
            lines.append(
                "⚠️ 禁止输出英文说明、Markdown 表格/摘要、「修改完成」「已写入文件」等对话文字；"
                "禁止 ` ```json ` 围栏。content 必须整段为可 `json.loads` 的阶段二裸 JSON。"
            )

    return "\n".join(lines)


def parse_previous_for_cheat(raw: str | None) -> dict[str, Any] | None:
    return _try_parse_obj(raw or "")
