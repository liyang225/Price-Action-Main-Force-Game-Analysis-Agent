"""Full stage-1 gate_trace fixtures for validator coherence tests."""
from __future__ import annotations

from typing import Any

from pa_agent.ai.coherence_checks import STAGE1_MANDATORY_GATE_NODES


def make_mandatory_gate_trace_proceed(
    *,
    cycle_branch: str = "normal_channel",
    direction_branch: str = "bullish",
    max_seq: int = 12,
) -> list[dict[str, Any]]:
    """Minimal gate_trace covering all mandatory nodes with distinct bar_range."""
    cap = max(2, max_seq)
    items: list[dict[str, Any]] = []
    for i, nid in enumerate(STAGE1_MANDATORY_GATE_NODES):
        hi = max(1, cap - (i % cap))
        lo = max(1, hi - 1 - (i % 2))
        if lo >= hi:
            bar_range = f"K{hi}"
        else:
            bar_range = f"K{hi}-K{lo}"
        reason = (
            f"节点{nid}依据当前K线窗口{bar_range}完成定性判断，"
            f"结构与周期识别一致，可继续后续分析。"
        )
        if nid == STAGE1_MANDATORY_GATE_NODES[-1]:
            reason += " 闸门通过，可进入阶段二。"
        item: dict[str, Any] = {
            "node_id": nid,
            "question": f"节点 {nid}",
            "answer": "是",
            "reason": reason,
            "bar_range": bar_range,
        }
        if nid == "1.2":
            item["branch"] = cycle_branch
        if nid == "2.3":
            item["branch"] = direction_branch
        items.append(item)
    return items


def make_bar_by_bar_summary(count: int) -> list[dict[str, Any]]:
    """Build *count* bar_by_bar_summary rows (K1..Kcount)."""
    rows: list[dict[str, Any]] = []
    for i in range(1, count + 1):
        rows.append(
            {
                "bar": f"K{i}",
                "role": "structure",
                "bar_type": "trend_bull",
                "context_effect": "strengthens_bull",
                "follow_through": "pending" if i == 1 else "yes",
                "trapped_side": "none",
                "reason": f"棒 K{i}",
            }
        )
    return rows
