"""Binary decision tree loader and trace helpers (方案 A)."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pa_agent.config.paths import PROMPT_DIR

_BINARY_DECISION_FILE = "二元决策.txt"

_SECTION_RE = re.compile(r"^##\s+(\d+)\.\s+(.+)$")
_NODE_RE = re.compile(r"^###\s+([\d.]+[A-Z]?)\s+(.+)$")
_NODE_HEADER_LINE_RE = re.compile(r"^###\s+([\d.]+[A-Z]?)\s+")
_BRANCH_OUTCOME_LINE_RE = re.compile(
    r"^(是|否)(?:[，,]([^：:\n]+))?[：:]\s*(.*)$"
)
_BRANCH_OUTCOME_STOP_RE = re.compile(
    r"^(处理|判断依据|条件|说明|多头条件|空头条件|上涨|下跌)[：:]"
)
_BAR_RANGE_RE = re.compile(r"^K(\d+)-K(\d+)$", re.IGNORECASE)
_SINGLE_BAR_RE = re.compile(r"^K(\d+)$", re.IGNORECASE)
_QUESTION_BAR_BASIS_SUFFIX_RE = re.compile(r"（基于[^）]+判断）$")

GATE_RESULTS = frozenset({"proceed", "wait", "unknown"})
TRACE_ANSWERS = frozenset({"是", "否", "中性", "等待", "不适用"})
TERMINAL_OUTCOMES = frozenset({"wait", "reject", "trade", "proceed"})

# 阶段一禁止当作闸门的节点（原则/执行层，非诊断闸门）
STAGE1_FORBIDDEN_GATE_NODES = frozenset({"0.3"})


def _gate_trace_sort_key(node_id: str) -> tuple[int, int, str]:
    """Numeric sort key for gate_trace node_id values used by the ordering validator.

    Converts '1.1' -> (1, 1, '1.1'), '2.3' -> (2, 3, '2.3') so that chapter-
    section pairs sort in natural document order.  Non-numeric suffixes fall
    back to string comparison in the third component.
    """
    parts = str(node_id or "").split(".", 1)
    try:
        major = int(parts[0])
    except (ValueError, IndexError):
        return (999, 999, node_id)
    if len(parts) == 1:
        return (major, 0, node_id)
    sub = parts[1]
    try:
        return (major, int(sub), node_id)
    except ValueError:
        return (major, 999, node_id)

def _node_sort_key(node_id: str) -> tuple[int, str]:
    """Sort key for decision_trace ordering checks."""
    prefixes = (
        ("3.", 30),
        ("4.", 40),
        ("5.", 50),
        ("6.", 60),
        ("7.", 70),
        ("8.", 80),
        ("9.", 90),
        ("10.1", 101),
        ("10.2", 102),
        ("10.3", 103),
        ("11.", 110),
        ("12.", 120),
        ("13.", 130),
        ("14.", 140),
    )
    for prefix, rank in prefixes:
        if node_id.startswith(prefix) or node_id == prefix.rstrip("."):
            return (rank, node_id)
    return (999, node_id)


def _trace_node_ids(trace: list[dict[str, Any]]) -> list[str]:
    return [str(x.get("node_id", "")) for x in trace if isinstance(x, dict) and x.get("node_id")]


def _index_of(nodes: list[str], node_id: str) -> int:
    try:
        return nodes.index(node_id)
    except ValueError:
        return -1


def _collect_branch_outcome(block_lines: list[str], branch_char: str) -> str:
    """Extract summary text after ``是：`` / ``否，…：`` in a node block."""
    collecting = False
    parts: list[str] = []
    qualifier = ""

    def _flush_qualifier() -> None:
        nonlocal qualifier
        if qualifier and (not parts or parts[-1] != qualifier):
            parts.append(qualifier)
        qualifier = ""

    for raw in block_lines:
        line = raw.strip()
        if not line:
            if collecting and parts:
                break
            continue
        m = _BRANCH_OUTCOME_LINE_RE.match(line)
        if m:
            if m.group(1) == branch_char:
                collecting = True
                qualifier = (m.group(2) or "").strip()
                rest = (m.group(3) or "").strip()
                if qualifier and rest:
                    parts.append(f"{qualifier}：{rest}")
                    qualifier = ""
                elif rest:
                    parts.append(rest)
                elif qualifier:
                    parts.append(qualifier)
                    qualifier = ""
            elif collecting:
                break
            continue
        if collecting:
            if _BRANCH_OUTCOME_LINE_RE.match(line):
                if parts:
                    break
                continue
            if line.startswith("###") or line.startswith("##"):
                break
            if _BRANCH_OUTCOME_STOP_RE.match(line):
                break
            bullet = line.lstrip("-• ").strip()
            if qualifier:
                parts.append(f"{qualifier}：{bullet}")
                qualifier = ""
            else:
                parts.append(bullet)
            if len(parts) >= 5:
                break
    _flush_qualifier()
    text = " · ".join(p for p in parts if p)
    if len(text) > 140:
        return text[:137] + "…"
    return text


def _parse_all_branch_outcomes(text: str) -> dict[str, dict[str, str]]:
    """Map node_id -> {branch_yes, branch_no} from decision tree source text."""
    outcomes: dict[str, dict[str, str]] = {}
    chunks = re.split(r"(?=^###\s+[\d.])", text, flags=re.MULTILINE)
    for chunk in chunks:
        header = chunk.strip().splitlines()[0] if chunk.strip() else ""
        hm = _NODE_HEADER_LINE_RE.match(header)
        if not hm:
            continue
        nid = hm.group(1)
        lines = chunk.splitlines()[1:]
        outcomes[nid] = {
            "branch_yes": _collect_branch_outcome(lines, "是"),
            "branch_no": _collect_branch_outcome(lines, "否"),
        }
    return outcomes


def get_node_branch_outcome(node_id: str, branch: str) -> str:
    """Return human-readable outcome for the ``是`` or ``否`` arm (branch=yes|no)."""
    tree = load_decision_tree()
    node = tree.get("node_index", {}).get(str(node_id), {})
    key = "branch_yes" if branch in ("yes", "是", "y") else "branch_no"
    text = str(node.get(key, "") or "").strip()
    if text:
        return text
    return "继续下一步" if key == "branch_yes" else "等待 / 切换逻辑"


@lru_cache(maxsize=1)
def load_decision_tree(path: Path | None = None) -> dict[str, Any]:
    """Parse ``二元决策.txt`` into sections + nodes for the UI tree."""
    txt_path = path or (PROMPT_DIR / _BINARY_DECISION_FILE)
    text = txt_path.read_text(encoding="utf-8")
    branch_outcomes = _parse_all_branch_outcomes(text)
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in text.splitlines():
        sec_m = _SECTION_RE.match(line)
        if sec_m:
            if current is not None:
                sections.append(current)
            current = {
                "id": sec_m.group(1),
                "title": sec_m.group(2).strip(),
                "nodes": [],
            }
            continue

        node_m = _NODE_RE.match(line)
        if node_m and current is not None:
            current["nodes"].append(
                {
                    "id": node_m.group(1),
                    "question": node_m.group(2).strip(),
                }
            )

    if current is not None:
        sections.append(current)

    node_index: dict[str, dict[str, Any]] = {}
    for sec in sections:
        for node in sec["nodes"]:
            nid = node["id"]
            bo = branch_outcomes.get(nid, {})
            node_index[nid] = {
                **node,
                "section_id": sec["id"],
                "section_title": sec["title"],
                "branch_yes": bo.get("branch_yes", ""),
                "branch_no": bo.get("branch_no", ""),
            }

    return {
        "version": 1,
        "source": txt_path.name,
        "sections": sections,
        "node_index": node_index,
    }


def normalize_bar_range(item: dict[str, Any], *, default_max_seq: int | None = None) -> str:
    """Return display token like ``K50-K1`` or ``K1`` from a trace item.

    When *default_max_seq* is provided, K0 references (unclosed bar) are
    automatically capped to K1 so that downstream validators see valid seqs.
    """
    from pa_agent.ai.trace_normalize import fix_bar_range_string

    raw = item.get("bar_range")
    if raw is not None and str(raw).strip():
        return fix_bar_range_string(str(raw), default_max_seq=default_max_seq)

    bar_from = item.get("bar_from")
    bar_to = item.get("bar_to")
    if bar_from is not None and bar_to is not None:
        bf, bt = int(bar_from), int(bar_to)
        if bf == bt:
            return f"K{bf}"
        return f"K{max(bf, bt)}-K{min(bf, bt)}"
    return ""


_BRANCH_DISPLAY_ZH = {
    "bullish": "多头",
    "bearish": "空头",
    "neutral": "中性",
    "yes": "是",
    "no": "否",
    "lower": "下边界",
    "upper": "上边界",
    "middle": "中间",
    "trading_range": "普通交易区间",
    "trending_tr": "趋势型交易区间",
    "pullback": "楔形回撤",
    "reversal": "楔形反转",
    "path_a": "路径A",
    "path_b": "路径B",
    "path_c": "路径C",
    "AIL": "Always In 多头",
    "AIS": "Always In 空头",
}


def strip_question_bar_basis_suffix(question: str) -> str:
    """Remove trailing ``（基于K…判断）`` from question text (AI or legacy UI)."""
    q = str(question or "").strip()
    while True:
        m = _QUESTION_BAR_BASIS_SUFFIX_RE.search(q)
        if not m:
            break
        q = q[: m.start()].strip()
    return q


def plain_trace_question(item: dict[str, Any]) -> str:
    """Question only — K-line basis belongs in bar_range / dedicated column."""
    q = str(item.get("question", item.get("node_id", ""))).strip()
    return strip_question_bar_basis_suffix(q)


def format_trace_answer(item: dict[str, Any]) -> str:
    """Display answer (+ branch label when present)."""
    if item.get("skipped") and str(item.get("answer", "")) == "不适用":
        return "不适用"
    ans = str(item.get("answer", "") or "").strip()
    branch = item.get("branch")
    if branch is not None and str(branch).strip():
        b = str(branch).strip()
        bzh = _BRANCH_DISPLAY_ZH.get(b, b)
        if bzh and bzh != ans:
            return f"{ans}（{bzh}）" if ans else bzh
    return ans


def format_bar_basis_suffix(item: dict[str, Any]) -> str:
    """Format UI suffix, e.g. ``（基于K50-K1判断）``."""
    if item.get("skipped") and item.get("answer") == "不适用":
        return ""
    br = normalize_bar_range(item)
    if not br:
        return ""
    if br in ("不适用", "—", "-", "N/A"):
        return ""
    if br.startswith("（"):
        return br if "判断" in br else f"{br}判断）"
    return f"（基于{br}判断）"


def question_with_bar_basis(item: dict[str, Any]) -> str:
    """Legacy: question + basis suffix. Prefer ``plain_trace_question`` in new UI."""
    q = plain_trace_question(item)
    suffix = format_bar_basis_suffix(item)
    if suffix and suffix not in q:
        return f"{q}{suffix}"
    return q


def validate_bar_range_field(item: dict[str, Any], path: str) -> list[str]:
    """Validate bar_range on one trace item."""
    errors: list[str] = []
    if item.get("skipped") and item.get("answer") == "不适用":
        return errors

    br = normalize_bar_range(item)
    if not br:
        errors.append(f"{path}: bar_range is required (e.g. K50-K1)")
        return errors

    if br in ("不适用", "—"):
        return errors

    if "填写" in br or br.startswith("<") or "由你" in br:
        errors.append(f"{path}: bar_range must be actual K-line seqs from the table, not a placeholder")
        return errors

    text = br.upper().replace(" ", "")
    m = _BAR_RANGE_RE.match(text)
    if m:
        older, newer = int(m.group(1)), int(m.group(2))
        if older < newer:
            errors.append(
                f"{path}: bar_range K{older}-K{newer} invalid; "
                "older bar must have larger seq (e.g. K50-K1)"
            )
        return errors

    if _SINGLE_BAR_RE.match(text):
        return errors

    errors.append(f"{path}: bar_range must look like K50-K1 or K1 (seq1=newest bar)")
    return errors


def node_label(node_id: str, tree: dict[str, Any] | None = None) -> str:
    """Return human question text for a node id."""
    if tree is None:
        tree = load_decision_tree()
    entry = tree.get("node_index", {}).get(node_id)
    if entry:
        return str(entry.get("question", node_id))
    return node_id


def merge_traces(
    gate_trace: list[dict[str, Any]] | None,
    decision_trace: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Combine Stage1 gate_trace and Stage2 decision_trace in walk order."""
    merged: list[dict[str, Any]] = []
    for item in gate_trace or []:
        if isinstance(item, dict):
            merged.append({**item, "phase": "gate"})
    for item in decision_trace or []:
        if isinstance(item, dict):
            merged.append({**item, "phase": "decision"})
    return merged


def build_stage2_gate_wait_response(stage1_json: dict[str, Any]) -> dict[str, Any]:
    """Synthesize Stage2 JSON when Stage1 ``gate_result`` blocks trading."""
    gate_trace = stage1_json.get("gate_trace") or []
    last = gate_trace[-1] if gate_trace else {}
    node_id = str(last.get("node_id", "1.3"))
    label = str(last.get("reason") or last.get("action") or "闸门未通过，等待更清晰信号")

    cycle = stage1_json.get("cycle_position", "")
    direction = stage1_json.get("direction", "")
    key_signals = stage1_json.get("key_signals") or []

    return {
        "decision": {
            "order_direction": None,
            "order_type": "不下单",
            "entry_price": None,
            "take_profit_price": None,
            "take_profit_price_2": None,
            "stop_loss_price": None,
            "reasoning": (
                f"阶段一闸门结论为「{stage1_json.get('gate_result', 'wait')}」，"
                f"在节点 {node_id} 停止：{label}。"
                "未进入阶段二策略分支评估。"
            ),
            "diagnosis_confidence": stage1_json.get("diagnosis_confidence", 0),
            "diagnosis_confidence_reasoning": (
                stage1_json.get("risk_warning")
                or f"闸门在 {node_id}，市场诊断置信度沿用阶段一。"
            ),
            "trade_confidence": 0,
            "trade_confidence_reasoning": "闸门未通过，不执行交易决策。",
            "estimated_win_rate": None,
            "estimated_win_rate_reasoning": "未进入 §10.3，无交易胜率估计。",
            "key_factors": list(key_signals)[:5],
            "watch_points": ["等待结构明朗或闸门节点转为可继续"],
            "risk_assessment": stage1_json.get("risk_warning") or "闸门等待",
            "invalidation_condition": None,
        },
        "diagnosis_summary": {
            "cycle_position": cycle,
            "direction": direction,
            "key_signals": list(key_signals),
        },
        "decision_trace": [],
        "terminal": {
            "node_id": node_id,
            "outcome": "wait",
            "label": label,
        },
        "gate_shortcircuited": True,
        "next_bar_prediction": {
            "direction": None,
            "probabilities": None,
            "reasoning": (
                f"阶段一闸门结论为「{stage1_json.get('gate_result', 'wait')}」（节点 {node_id}），"
                "未进入策略分支评估，亦不预测下一根 K 线方向。"
            ),
            "unpredictable": True,
            "features_used": ["stage1_diagnosis"],
        },
        "next_cycle_prediction": {
            "cycle": None,
            "direction": None,
            "probabilities": None,
            "reasoning": (
                f"阶段一闸门未通过（{node_id}），未进入阶段二周期演变评估。"
            ),
            "unpredictable": True,
            "features_used": ["stage1_diagnosis"],
        },
    }


def validate_gate_result_consistency(stage1: dict[str, Any]) -> list[str]:
    """Return list of consistency error messages (empty if ok)."""
    errors: list[str] = []
    gate_result = stage1.get("gate_result")
    if gate_result not in GATE_RESULTS:
        errors.append(f"gate_result must be one of {sorted(GATE_RESULTS)}")
        return errors

    trace = stage1.get("gate_trace")
    if not isinstance(trace, list) or not trace:
        errors.append("gate_trace must be a non-empty array")
        return errors

    for i, item in enumerate(trace):
        if not isinstance(item, dict):
            errors.append(f"gate_trace[{i}] must be an object")
            continue
        nid = str(item.get("node_id", ""))
        if nid in STAGE1_FORBIDDEN_GATE_NODES:
            errors.append(
                f"gate_trace[{i}] node {nid}: 交易者方程不得在阶段一评估，"
                "请在阶段二节点 10.3（止损止盈确定后）评估"
            )
        ans = item.get("answer")
        if ans not in TRACE_ANSWERS:
            errors.append(f"gate_trace[{i}].answer invalid: {ans!r}")
        errors.extend(validate_bar_range_field(item, f"gate_trace[{i}]"))

    if gate_result in ("wait", "unknown"):
        last = trace[-1]
        if isinstance(last, dict) and last.get("answer") not in ("否", "等待"):
            errors.append(
                "gate_result wait/unknown should end with answer 否 or 等待 on last gate node"
            )

    # Check node_id ordering: gate_trace must be in ascending chapter-section order.
    # merge_program_nodes now sorts injected nodes, but validate here to catch any
    # future regression or manually constructed traces with wrong ordering.
    #
    # Exception: when gate_result=wait/unknown, merge_program_nodes_head prepends
    # program nodes so the AI's terminating node (answer=否/等待) stays at the tail.
    # That terminal node is intentionally out of numeric order — skip the ordering
    # check for the last node in wait/unknown traces.
    node_ids = [
        str(item.get("node_id", ""))
        for item in trace
        if isinstance(item, dict) and item.get("node_id")
    ]
    terminal_exempt = gate_result in ("wait", "unknown") and len(node_ids) > 1
    check_up_to = len(node_ids) - 1 if terminal_exempt else len(node_ids)
    for idx in range(1, check_up_to):
        prev_key = _gate_trace_sort_key(node_ids[idx - 1])
        curr_key = _gate_trace_sort_key(node_ids[idx])
        if curr_key < prev_key:
            errors.append(
                f"gate_trace node ordering error: {node_ids[idx - 1]} "
                f"should come before {node_ids[idx]} "
                f"(章节顺序错乱，程序节点注入后未正确排序)"
            )

    return errors


def validate_stage2_trace_consistency(stage2: dict[str, Any]) -> list[str]:
    """Return list of consistency error messages (empty if ok)."""
    errors: list[str] = []
    if stage2.get("gate_shortcircuited"):
        return errors

    terminal = stage2.get("terminal")
    if not isinstance(terminal, dict):
        errors.append("terminal must be an object")
        return errors

    outcome = terminal.get("outcome")
    if outcome not in TERMINAL_OUTCOMES:
        errors.append(f"terminal.outcome invalid: {outcome!r}")

    trace = stage2.get("decision_trace")
    if not isinstance(trace, list) or not trace:
        errors.append("decision_trace must be a non-empty array when not gate-shortcircuited")
        return errors

    decision = stage2.get("decision", {})
    order_type = decision.get("order_type") if isinstance(decision, dict) else None

    if order_type == "不下单" and outcome == "trade":
        errors.append('order_type 不下单 cannot pair with terminal.outcome "trade"')
    if order_type in ("限价单", "突破单", "市价单") and outcome in ("wait", "reject"):
        errors.append(
            f"order_type {order_type} cannot pair with terminal.outcome {outcome!r}"
        )
    if (
        order_type in ("限价单", "突破单", "市价单")
        and outcome == "trade"
        and str(terminal.get("node_id", "") or "").strip().startswith("14")
    ):
        errors.append(
            "terminal.node_id must not be §14 for a trade; §14 is only a prohibition scan, "
            "use the final §11 order node or 10.3"
        )

    for i, item in enumerate(trace):
        if isinstance(item, dict):
            errors.extend(validate_bar_range_field(item, f"decision_trace[{i}]"))

    node_ids = _trace_node_ids(trace)

    if "0.3" in node_ids:
        errors.append(
            "decision_trace must not include 0.3; use 10.3 after stop/target are set"
        )

    # §10 子节点顺序：10.1 → 10.2 → 10.3
    for a, b in (("10.1", "10.2"), ("10.2", "10.3")):
        ia, ib = _index_of(node_ids, a), _index_of(node_ids, b)
        if ia >= 0 and ib >= 0 and ia > ib:
            errors.append(f"decision_trace order: {a} must appear before {b}")

    idx_103 = _index_of(node_ids, "10.3")
    idx_11 = next((i for i, n in enumerate(node_ids) if n.startswith("11.")), -1)

    if order_type in ("限价单", "突破单", "市价单"):
        if idx_103 < 0:
            errors.append(
                "order_type requires trade: decision_trace must include node 10.3 "
                "(交易者方程) with numeric stop/target assessment"
            )
        else:
            item_103 = trace[idx_103]
            if isinstance(item_103, dict) and item_103.get("answer") != "是":
                errors.append("node 10.3 must be 是 when placing an order")
            if idx_11 >= 0 and idx_11 < idx_103:
                errors.append("§11 下单方式 nodes must appear after 10.3 交易者方程")
            # §9 须在 §10 之前
            idx_9 = next((i for i, n in enumerate(node_ids) if n.startswith("9.")), -1)
            idx_101 = _index_of(node_ids, "10.1")
            if idx_9 >= 0 and idx_101 >= 0 and idx_9 > idx_101:
                errors.append("§9 入场信号 must appear before §10.1 止损")

    if order_type == "不下单" and outcome in ("wait", "reject"):
        term_nid = str(terminal.get("node_id", ""))
        if idx_103 >= 0 and term_nid not in ("10.3", "10.2", "10.1", "9.5", "9.2"):
            pass  # allow other legitimate early stops
        if idx_103 >= 0 and trace[idx_103].get("answer") == "否" and term_nid != "10.3":
            errors.append(
                "when 10.3 answer is 否, terminal.node_id should be 10.3"
            )

        # Detect the "no entry plan → terminal=reject" anti-pattern.
        # When §9.0 answer is 否/等待 (no valid signal bar) or §10.1 answer is 否
        # (no stop-loss anchor), there is no trade plan to evaluate.
        # In that case outcome="reject" is semantically wrong (you can't reject a
        # plan that never existed) — outcome must be "wait".
        #
        # Exception: if §14 is genuinely violated (answer=是 AND reason does NOT
        # contain denial phrases), outcome="reject" is correct because the
        # prohibition explicitly terminated the analysis.  §14 is a stronger
        # stop reason than §9.0=否 and takes semantic precedence.
        idx_90 = _index_of(node_ids, "9.0")
        idx_101 = _index_of(node_ids, "10.1")
        idx_90p = _index_of(node_ids, "9.0P")
        no_entry_plan = False
        if idx_90 >= 0:
            ans_90 = str(trace[idx_90].get("answer", "") or "").strip()
            if ans_90 in ("否", "等待"):
                if idx_90p >= 0:
                    ans_90p = str(trace[idx_90p].get("answer", "") or "").strip()
                    if ans_90p == "是":
                        no_entry_plan = False
                    else:
                        no_entry_plan = True
                else:
                    no_entry_plan = True
        if not no_entry_plan and idx_101 >= 0:
            ans_101 = str(trace[idx_101].get("answer", "") or "").strip()
            if ans_101 == "否":
                no_entry_plan = True
        if no_entry_plan and outcome == "reject":
            # Check if §14 genuinely triggered (true violation, not AI misuse)
            _DENIAL = ("未触犯", "未违反", "无触犯", "无违规", "通过扫描", "扫描通过", "无禁止", "未触发")
            sec14_genuinely_violated = any(
                str(item.get("node_id", "")).strip().startswith("14")
                and str(item.get("answer", "")).strip() == "是"
                and not any(p in str(item.get("reason", "") or "") for p in _DENIAL)
                for item in trace
                if isinstance(item, dict)
            )
            if not sec14_genuinely_violated:
                errors.append(
                    "terminal.outcome should be 'wait' (not 'reject') when there is no "
                    "entry plan to evaluate: §9.0=否/等待 or §10.1=否 means no trade "
                    "scheme existed, so nothing can be 'rejected' by the trader equation"
                )

    # 整体章节顺序：已评估节点的 rank 应非递减
    ranks = [_node_sort_key(n)[0] for n in node_ids if not n.startswith("0.")]
    for i in range(1, len(ranks)):
        if ranks[i] < ranks[i - 1]:
            errors.append(
                f"decision_trace chapter order violation near node {node_ids[i]}"
            )
            break

    return errors
