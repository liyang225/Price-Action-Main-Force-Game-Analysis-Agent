"""Semantic validation for gate_trace / decision_trace (beyond schema/enums)."""
from __future__ import annotations

import logging
import re
from typing import Any

from pa_agent.ai.decision_tree import load_decision_tree

logger = logging.getLogger(__name__)

_K_REF_RE = re.compile(r"K\s*(\d+)", re.IGNORECASE)
_BOILERPLATE_REASONS = frozenset(
    {
        "结构清晰",
        "继续",
        "通过",
        "符合",
        "是",
        "否",
        "—",
        "-",
    }
)

_PROCEED_FINAL_TOKENS = (
    "进入阶段二",
    "可进入阶段二",
    "闸门通过",
    "继续阶段二",
    "进入策略",
    "可继续分析",
    "proceed",
)

_REASON_REQUIRED_NODE_IDS = frozenset({"10.3"})


def _trace_reason_required(item: dict[str, Any]) -> bool:
    """Return True when this trace node must have non-empty reason text."""
    nid = str(item.get("node_id", "") or "").strip()
    return nid in _REASON_REQUIRED_NODE_IDS


_ORDER_SECTION_9_REQUIRED = frozenset({"9.0", "9.1", "9.2", "9.3", "9.4", "9.5", "9.6", "9.7"})
_PRICE_IN_REASON_RE = re.compile(r"\d+(?:\.\d+)?")


def _bar_seqs_from_range(bar_range: str) -> set[int]:
    text = (bar_range or "").strip().upper().replace(" ", "")
    if not text or text in ("不适用", "—", "全局", "GLOBAL"):
        return set()
    m = re.match(r"^K(\d+)-K(\d+)$", text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a < b:
            logger.warning(
                "bar_range=%r has reversed order (K%d-K%d); K1=newest, K{N}=older. "
                "Auto-corrected but this may indicate model confusion.",
                text, a, b,
            )
        lo, hi = min(a, b), max(a, b)
        return set(range(lo, hi + 1))
    m1 = re.match(r"^K(\d+)$", text)
    if m1:
        return {int(m1.group(1))}
    return set()


def _bar_seqs_from_reason(reason: str) -> set[int]:
    return {int(m.group(1)) for m in _K_REF_RE.finditer(reason or "")}


def _node_questions() -> dict[str, str]:
    tree = load_decision_tree()
    index = tree.get("node_index", {})
    out: dict[str, str] = {}
    for nid, node in index.items():
        q = str(node.get("question", "") or "").strip()
        if q:
            out[str(nid)] = q
    return out


def _normalize_question_text(text: str) -> str:
    """Collapse spacing / Always-In variants for fuzzy question match."""
    s = (text or "").strip().lower()
    s = re.sub(r"\s+", "", s)
    s = s.replace("alwaysin", "always_in").replace("always_in", "always_in")
    s = s.replace("支撑", "支持")
    return s


def _channel_direction_question_ok(question: str) -> bool:
    """Accept 4.2 paraphrases such as 「通道方向是否为下跌？」."""
    qn = _normalize_question_text(question)
    return "通道方向" in qn and ("上涨" in qn or "下跌" in qn)


def _question_matches_tree(expected: str, question: str, *, node_id: str = "") -> bool:
    if not expected or not question:
        return True
    if question.startswith("节点"):
        return True
    if node_id == "4.2" and _channel_direction_question_ok(question):
        return True
    if expected in question or question in expected:
        return True
    exp_n = _normalize_question_text(expected)
    q_n = _normalize_question_text(question)
    if not exp_n or not q_n:
        return True
    if exp_n in q_n or q_n in exp_n:
        return True
    head = min(10, len(exp_n), len(q_n))
    return exp_n[:head] == q_n[:head]


def validate_trace_semantics(
    trace: list[dict[str, Any]] | None,
    *,
    path_prefix: str,
    stage: str,
    gate_result: str | None = None,
) -> list[str]:
    """Return human-readable semantic errors for a trace list."""
    if not isinstance(trace, list):
        return []

    errors: list[str] = []
    node_questions = _node_questions()
    reasons_seen: list[str] = []
    node_ids: list[str] = []

    for i, item in enumerate(trace):
        if not isinstance(item, dict):
            continue
        nid = str(item.get("node_id", "") or "").strip()
        if nid:
            node_ids.append(nid)

        # Skip semantic checks for auto-injected nodes (added by coherence_checks,
        # not by the model — their question text intentionally differs from the tree).
        if item.get("_auto_injected"):
            continue

        if item.get("skipped") and item.get("answer") == "不适用":
            continue

        reason = str(item.get("reason", "") or "").strip()
        if _trace_reason_required(item):
            if not reason:
                errors.append(f"{path_prefix}[{i}].reason: required non-empty string")
            elif reason in _BOILERPLATE_REASONS:
                errors.append(f"{path_prefix}[{i}].reason: boilerplate text not allowed")
        elif reason and reason in _BOILERPLATE_REASONS:
            errors.append(f"{path_prefix}[{i}].reason: boilerplate text not allowed")

        if reason:
            if reason in reasons_seen:
                errors.append(
                    f"{path_prefix}[{i}].reason: duplicate reason text across trace nodes"
                )
            reasons_seen.append(reason)

        br = str(item.get("bar_range", "") or "").strip()
        cited = _bar_seqs_from_reason(reason)
        if cited and br and br not in ("不适用", "—", "全局"):
            allowed = _bar_seqs_from_range(br)
            if allowed and not cited.issubset(allowed):
                outside = sorted(cited - allowed)
                errors.append(
                    f"{path_prefix}[{i}]: reason cites K-lines outside bar_range {br!r} "
                    f"(extra: {', '.join(f'K{s}' for s in outside)})"
                )

        if nid and nid in node_questions:
            expected = node_questions[nid]
            question = str(item.get("question", "") or "").strip()
            if question and expected and not _question_matches_tree(
                expected, question, node_id=nid
            ):
                errors.append(
                    f"{path_prefix}[{i}].question: should match decision tree "
                    f"node {nid} (canonical: {expected[:24]}…)"
                )

    if stage == "stage1" and str(gate_result or "").lower() == "proceed" and trace:
        last = trace[-1] if isinstance(trace[-1], dict) else {}
        blob = str(last.get("reason", "") or "")
        if not any(tok in blob for tok in _PROCEED_FINAL_TOKENS):
            errors.append(
                f"{path_prefix}: last gate_trace reason should state proceed/wait rationale "
                "(e.g. 进入阶段二 / 闸门通过)"
            )

    return errors


def validate_stage2_order_trace_semantics(stage2: dict[str, Any]) -> list[str]:
    """Extra semantics when placing an order."""
    decision = stage2.get("decision")
    if not isinstance(decision, dict):
        return []
    if decision.get("order_type") not in ("限价单", "突破单", "市价单"):
        return []

    errors: list[str] = []
    trace = stage2.get("decision_trace")
    if not isinstance(trace, list):
        return ["decision_trace: required list when placing an order"]

    node_ids = [
        str(x.get("node_id", "")) for x in trace if isinstance(x, dict) and x.get("node_id")
    ]
    has_9 = any(n.startswith("9.") for n in node_ids)
    if not has_9:
        errors.append("placing an order requires decision_trace nodes in §9 (9.x)")

    item_90 = next(
        (x for x in trace if isinstance(x, dict) and str(x.get("node_id")) == "9.0"),
        None,
    )
    item_90p = next(
        (x for x in trace if isinstance(x, dict) and str(x.get("node_id")) == "9.0P"),
        None,
    )
    if (
        decision.get("order_type") == "限价单"
        and isinstance(item_90, dict)
        and str(item_90.get("answer", "") or "").strip() in ("否", "等待")
        and not (
            isinstance(item_90p, dict)
            and str(item_90p.get("answer", "") or "").strip() == "是"
        )
    ):
        errors.append(
            "limit order with §9.0=否/等待 requires §9.0P=是 (background limit path)"
        )

    for required in ("10.1", "10.2", "10.3"):
        if required not in node_ids:
            errors.append(f"placing an order requires decision_trace node {required}")

    item_103 = next(
        (x for x in trace if isinstance(x, dict) and str(x.get("node_id")) == "10.3"),
        None,
    )
    if isinstance(item_103, dict):
        reason = str(item_103.get("reason", "") or "")
        if not _PRICE_IN_REASON_RE.search(reason):
            errors.append(
                "decision_trace[10.3].reason must mention numeric entry/stop/target "
                "or equation values used"
            )

    return errors
