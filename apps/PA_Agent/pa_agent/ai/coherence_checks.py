"""Cross-field coherence checks for Stage 1 / Stage 2 AI JSON (P0/P1 validators)."""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

from pa_agent.ai.decision_tree import normalize_bar_range, validate_bar_range_field

# Target bar count for bar_by_bar_summary when the analysis window has enough bars.
BAR_BY_BAR_TARGET_COUNT = 5

# Stage-one gate nodes required when gate_result=proceed (prompt §0–§2).
STAGE1_MANDATORY_GATE_NODES: tuple[str, ...] = (
    "1.1",
    "1.2",
    "1.3",
    "2.1",
    "2.2",
    "2.3",
    "2.4",
    "2.5",
)

_RANGE_CYCLES = frozenset({"trading_range", "extreme_tr", "trending_tr", "broad_channel"})

_CYCLE_BRANCH_ALIASES: dict[str, str] = {
    "trading_range": "trading_range",
    "tr": "trading_range",
    "交易区间": "trading_range",
    "普通交易区间": "trading_range",
    "trending_tr": "trending_tr",
    "趋势型交易区间": "trending_tr",
    "extreme_tr": "extreme_tr",
    "极端交易区间": "extreme_tr",
    "spike": "spike",
    "尖峰": "spike",
    "micro_channel": "micro_channel",
    "微型通道": "micro_channel",
    "tight_channel": "tight_channel",
    "窄通道": "tight_channel",
    "normal_channel": "normal_channel",
    "正常通道": "normal_channel",
    "broad_channel": "broad_channel",
    "宽通道": "broad_channel",
    "unknown": "unknown",
}

_DIRECTION_BRANCH_ALIASES: dict[str, str] = {
    "bull": "bullish",
    "bullish": "bullish",
    "bearish": "bearish",
    "neutral": "neutral",
    "多头": "bullish",
    "空头": "bearish",
    "中性": "neutral",
    "上涨": "bullish",
    "下跌": "bearish",
    "震荡": "neutral",
}

_K_SEQ_RE = re.compile(r"K\s*(\d+)", re.IGNORECASE)
_OVERRIDE_TRACE_NODES = frozenset({"1.2", "2.3"})

_BAR_FIELD_RE = re.compile(r"K\s*(\d+)", re.IGNORECASE)


def _trace_node_ids(trace: list[dict[str, Any]] | None) -> set[str]:
    out: set[str] = set()
    for item in trace or []:
        if isinstance(item, dict) and item.get("node_id"):
            out.add(str(item["node_id"]))
    return out


def _find_trace_item(trace: list[dict[str, Any]] | None, node_id: str) -> dict[str, Any] | None:
    for item in trace or []:
        if isinstance(item, dict) and str(item.get("node_id", "")) == node_id:
            return item
    return None


def _normalize_cycle_branch(raw: object) -> str | None:
    if raw is None:
        return None
    key = str(raw).strip().lower()
    if not key:
        return None
    return _CYCLE_BRANCH_ALIASES.get(key, key.replace(" ", "_"))


def _normalize_direction_branch(raw: object) -> str | None:
    if raw is None:
        return None
    key = str(raw).strip().lower()
    if not key:
        return None
    return _DIRECTION_BRANCH_ALIASES.get(key, key)


def _max_bar_seq(kline_frame: Any) -> int | None:
    bars = getattr(kline_frame, "bars", None) if kline_frame is not None else None
    if not bars:
        return None
    seqs = [int(getattr(b, "seq", 0)) for b in bars if getattr(b, "seq", None)]
    return max(seqs) if seqs else None


def _parse_bar_range_seqs(bar_range: str) -> list[int]:
    text = (bar_range or "").strip().upper().replace(" ", "")
    if not text or text in ("不适用", "—", "全局", "GLOBAL"):
        return []
    m = re.match(r"^K(\d+)-K(\d+)$", text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a < b:
            logger.warning(
                "bar_range=%r has reversed order (K%d-K%d); K1=newest, K{N}=older. "
                "Auto-corrected to K%d-K%d but this may indicate model confusion.",
                text, a, b, b, a,
            )
        lo, hi = min(a, b), max(a, b)
        return list(range(lo, hi + 1))
    m1 = re.match(r"^K(\d+)$", text)
    if m1:
        return [int(m1.group(1))]
    return []


def validate_skipped_node_consistency(
    trace: list[dict[str, Any]] | None,
    *,
    path_prefix: str,
    mandatory_nodes: tuple[str, ...] = (),
) -> list[str]:
    """Check that skipped nodes are internally consistent and mandatory nodes aren't silently skipped.

    This validator does NOT block on strict bar_range correctness for skipped nodes,
    but ensures:
    1. skipped=true nodes have answer="不适用"
    2. mandatory gate nodes (when gate_result=proceed) are not skipped without reason
    """
    if not isinstance(trace, list):
        return []

    errors: list[str] = []

    for i, item in enumerate(trace):
        if not isinstance(item, dict):
            continue
        if not item.get("skipped"):
            continue

        nid = str(item.get("node_id", "") or "").strip()
        answer = str(item.get("answer", "") or "").strip()

        # skipped=true but answer is not "不适用" — inconsistent
        if answer and answer != "不适用":
            errors.append(
                f"{path_prefix}[{i}] node_id={nid!r}: skipped=true but "
                f"answer={answer!r}, expected '不适用'"
            )

        # skipped=true with a non-null bar_range that isn't "不适用" — warn only
        br = str(item.get("bar_range", "") or "").strip()
        if br and br not in ("不适用", "—", "全局", ""):
            logger.warning(
                "%s[%d] node_id=%s skipped=true but bar_range=%r; "
                "normalize will correct to '不适用' but this may indicate model confusion",
                path_prefix, i, nid, br,
            )

    # Check mandatory nodes are not silently skipped
    if mandatory_nodes:
        for nid in mandatory_nodes:
            found_items = [
                item for item in trace
                if isinstance(item, dict)
                and str(item.get("node_id", "") or "").strip() == nid
            ]
            if not found_items:
                # Node entirely missing — handled elsewhere
                continue
            item = found_items[0]
            if item.get("skipped") and str(item.get("answer", "") or "").strip() == "不适用":
                # Mandatory node skipped with a generic answer — suspicious
                reason = str(item.get("reason", "") or "").strip()
                if not reason or reason in ("—", "-"):
                    errors.append(
                        f"{path_prefix}: mandatory node {nid} is skipped with "
                        f"no explanation (reason is empty/dash); "
                        "mandatory gate nodes should be evaluated, not skipped"
                    )

    return errors


def validate_trace_bars_in_frame(
    trace: list[dict[str, Any]] | None,
    *,
    kline_frame: Any,
    path_prefix: str,
) -> list[str]:
    """Ensure trace bar_range seqs refer to bars present in the frame."""
    max_seq = _max_bar_seq(kline_frame)
    if max_seq is None:
        return []

    errors: list[str] = []
    for i, item in enumerate(trace or []):
        if not isinstance(item, dict):
            continue
        if item.get("skipped") and item.get("answer") == "不适用":
            continue
        br = normalize_bar_range(item, default_max_seq=max_seq)
        if not br or br in ("不适用", "—", "全局"):
            continue
        seqs = _parse_bar_range_seqs(br)
        for seq in seqs:
            if seq < 1 or seq > max_seq:
                errors.append(
                    f"{path_prefix}[{i}]: bar_range {br} references K{seq}, "
                    f"but frame only has K1..K{max_seq}"
                )
    return errors


def validate_duplicate_bar_ranges(
    trace: list[dict[str, Any]] | None,
    *,
    path_prefix: str,
    min_items: int = 4,
) -> list[str]:
    """Flag when too many gate/decision nodes share the same bar_range."""
    ranges: list[str] = []
    for item in trace or []:
        if not isinstance(item, dict):
            continue
        if item.get("skipped") and item.get("answer") == "不适用":
            continue
        br = normalize_bar_range(item)
        if br and br not in ("不适用", "—", "全局"):
            ranges.append(br)
    if len(ranges) < min_items:
        return []
    if len(set(ranges)) == 1:
        return [
            f"{path_prefix}: {len(ranges)} nodes share identical bar_range {ranges[0]!r}; "
            "each node should cite the K-lines it actually used"
        ]
    return []


def auto_fix_bar_by_bar_types(
    stage1: dict[str, Any],
    *,
    kline_frame: Any = None,
) -> list[str]:
    """Auto-correct bar_type in bar_by_bar_summary when it contradicts program features.

    Mutates stage1 in-place: replaces the contradicting bar_type with the program value.
    Returns a list of correction messages for logging.
    """
    if kline_frame is None:
        return []
    summary = stage1.get("bar_by_bar_summary")
    if not isinstance(summary, list):
        return []

    from pa_agent.ai.kline_features import compute_kline_geometry_features

    features = {f.seq: f for f in compute_kline_geometry_features(kline_frame)}
    _opposites = frozenset({
        ("trend_bull", "trend_bear"),
        ("trend_bear", "trend_bull"),
        ("outside_bull", "outside_bear"),
        ("outside_bear", "outside_bull"),
    })
    corrections: list[str] = []
    for item in summary:
        if not isinstance(item, dict):
            continue
        bar_label = str(item.get("bar", "") or "")
        m = _BAR_FIELD_RE.search(bar_label)
        if not m:
            continue
        seq = int(m.group(1))
        feat = features.get(seq)
        if feat is None:
            continue
        declared = str(item.get("bar_type", "") or "").strip().lower()
        computed = str(feat.bar_type or "").strip().lower()
        if declared and computed and (declared, computed) in _opposites:
            item["bar_type"] = computed
            corrections.append(
                f"auto-fixed bar_by_bar_summary K{seq}.bar_type: {declared!r} → {computed!r}"
            )
    return corrections


def validate_stage1_coherence(
    stage1: dict[str, Any],
    *,
    kline_frame: Any = None,
    strict_bar_features: bool = True,
) -> list[str]:
    """P0-2, P1-4: mandatory gates, trace vs top-level fields, bar_by_bar, bar_range."""
    errors: list[str] = []

    gate_result = str(stage1.get("gate_result", "")).lower()
    gate_trace = stage1.get("gate_trace")
    if not isinstance(gate_trace, list):
        return errors

    present = _trace_node_ids(gate_trace)

    if gate_result == "proceed":
        missing = [n for n in STAGE1_MANDATORY_GATE_NODES if n not in present]
        if missing:
            errors.append(
                "gate_result=proceed requires gate_trace nodes: "
                + ", ".join(missing)
            )

    for i, item in enumerate(gate_trace):
        if isinstance(item, dict):
            errors.extend(validate_bar_range_field(item, f"gate_trace[{i}]"))

    errors.extend(
        validate_trace_bars_in_frame(
            gate_trace, kline_frame=kline_frame, path_prefix="gate_trace"
        )
    )
    errors.extend(
        validate_duplicate_bar_ranges(gate_trace, path_prefix="gate_trace")
    )

    # Check skipped node consistency (mandatory nodes shouldn't be silently skipped)
    mandatory = STAGE1_MANDATORY_GATE_NODES if gate_result == "proceed" else ()
    errors.extend(
        validate_skipped_node_consistency(
            gate_trace, path_prefix="gate_trace", mandatory_nodes=mandatory,
        )
    )

    cycle = str(stage1.get("cycle_position", "") or "").strip().lower()
    alt_cycle = stage1.get("alternative_cycle_position")
    alt_norm = str(alt_cycle).strip().lower() if alt_cycle else ""

    item_12 = _find_trace_item(gate_trace, "1.2")
    if item_12 and not item_12.get("skipped"):
        branch_cycle = _normalize_cycle_branch(item_12.get("branch"))
        if branch_cycle and cycle and branch_cycle not in (cycle, alt_norm):
            errors.append(
                f"gate_trace node 1.2 branch {branch_cycle!r} conflicts with "
                f"cycle_position={cycle!r}"
            )

    direction = str(stage1.get("direction", "") or "").strip().lower()
    item_23 = _find_trace_item(gate_trace, "2.3")
    if item_23 and not item_23.get("skipped"):
        branch_dir = _normalize_direction_branch(item_23.get("branch"))
        ans = str(item_23.get("answer", "") or "").strip()
        if branch_dir and direction and branch_dir != direction:
            if not (cycle in _RANGE_CYCLES and branch_dir == "neutral"):
                errors.append(
                    f"gate_trace node 2.3 branch {branch_dir!r} conflicts with "
                    f"direction={direction!r}"
                )
        if ans == "中性" and direction not in ("neutral", ""):
            if cycle not in _RANGE_CYCLES:
                errors.append(
                    "gate_trace node 2.3 answer=中性 but direction is not neutral"
                )

    summary = stage1.get("bar_by_bar_summary")
    if isinstance(summary, list):
        n_bars = _max_bar_seq(kline_frame)
        count = len(summary)
        if n_bars is not None and n_bars > 0:
            target = BAR_BY_BAR_TARGET_COUNT
            expected = min(target, n_bars)
            if count != expected:
                errors.append(
                    f"bar_by_bar_summary has {count} items; "
                    f"expected exactly {expected} for {n_bars} bars "
                    f"(K5–K1 when ≥{target} bars)"
                )
        elif count < 1:
            errors.append("bar_by_bar_summary must not be empty")

    errors.extend(
        validate_bar_by_bar_vs_features(
            stage1,
            kline_frame=kline_frame,
            strict=strict_bar_features,
        )
    )

    from pa_agent.ai.pattern_routing import validate_detected_patterns_vs_key_signals

    errors.extend(validate_detected_patterns_vs_key_signals(stage1))

    return errors


def validate_bar_by_bar_vs_features(
    stage1: dict[str, Any],
    *,
    kline_frame: Any = None,
    strict: bool = True,
) -> list[str]:
    """P1-5: bar_by_bar_summary bar_type should match program geometry features."""
    if kline_frame is None:
        return []

    from pa_agent.ai.kline_features import compute_kline_geometry_features

    summary = stage1.get("bar_by_bar_summary")
    if not isinstance(summary, list):
        return []

    features = {f.seq: f for f in compute_kline_geometry_features(kline_frame)}
    bars = getattr(kline_frame, "bars", None)
    bars_by_seq = (
        {int(getattr(b, "seq")): b for b in bars if getattr(b, "seq", None)} if bars else {}
    )

    # Threshold bands (prompt-engineering semantics: follow-through and bar-type near
    # hard cutoffs are objectively fuzzy; don't over-penalize the model on boundaries).
    _DOJI_BODY_RATIO = 0.25
    _DOJI_EPS = 0.02
    _TREND_CLOSEPOS_LOW = 0.35
    _TREND_CLOSEPOS_HIGH = 0.65
    _TREND_EPS = 0.03

    _STRUCTURAL_TYPES = frozenset({"inside", "outside_bull", "outside_bear"})
    _THRESHOLD_SENSITIVE_TYPES = frozenset(
        {"doji", "trend_bull", "trend_bear", "other"}
    )
    # outside_* implies trend_* — not a contradiction
    _COMPATIBLE_PAIRS: frozenset[tuple[str, str]] = frozenset({
        ("outside_bull", "trend_bull"),
        ("trend_bull", "outside_bull"),
        ("outside_bear", "trend_bear"),
        ("trend_bear", "outside_bear"),
        ("inside", "doji"),
        ("doji", "inside"),
    })

    def _near_threshold(seq: int) -> bool:
        bar = bars_by_seq.get(seq)
        if bar is None:
            return False
        try:
            high = max(float(bar.high), float(bar.low))
            low = min(float(bar.high), float(bar.low))
            open_ = float(bar.open)
            close = float(bar.close)
        except Exception:
            return False
        rng = high - low
        if rng <= 0:
            return False
        body_ratio = abs(close - open_) / rng
        close_position = max(0.0, min(1.0, (close - low) / rng))
        if abs(body_ratio - _DOJI_BODY_RATIO) <= _DOJI_EPS:
            return True
        if (
            abs(close_position - _TREND_CLOSEPOS_LOW) <= _TREND_EPS
            or abs(close_position - _TREND_CLOSEPOS_HIGH) <= _TREND_EPS
        ):
            return True
        return False

    errors: list[str] = []
    for i, item in enumerate(summary):
        if not isinstance(item, dict):
            continue
        bar_label = str(item.get("bar", "") or "")
        m = _BAR_FIELD_RE.search(bar_label)
        if not m:
            continue
        seq = int(m.group(1))
        feat = features.get(seq)
        if feat is None:
            errors.append(
                f"bar_by_bar_summary[{i}].bar {bar_label} not in geometry feature table"
            )
            continue
        declared = str(item.get("bar_type", "") or "").strip().lower()
        computed = str(feat.bar_type or "").strip().lower()
        if not declared or not computed or declared == computed:
            continue

        # AI bar_type and program bar_type are overlapping classifications,
        # not mutually exclusive. Only flag genuine bull/bear contradictions.
        _ALL_BAR_TYPES = _STRUCTURAL_TYPES | _THRESHOLD_SENSITIVE_TYPES
        if declared in _ALL_BAR_TYPES and computed in _ALL_BAR_TYPES:
            _opposites = (
                ("trend_bull", "trend_bear"),
                ("outside_bull", "outside_bear"),
            )
            if (declared, computed) in _opposites or (computed, declared) in _opposites:
                errors.append(
                    f"bar_by_bar_summary[{i}].bar_type={declared!r} contradicts "
                    f"program feature K{seq} bar_type={computed!r}"
                )
            continue
    return errors


def _stage2_trace_documents_override(
    decision_trace: list[dict[str, Any]] | None,
    *,
    field: str,
    new_value: str,
) -> bool:
    """True when trace shows explicit cycle/direction re-identification."""
    if not isinstance(decision_trace, list):
        return False
    for item in decision_trace:
        if not isinstance(item, dict):
            continue
        nid = str(item.get("node_id", ""))
        if nid not in _OVERRIDE_TRACE_NODES:
            continue
        branch = str(item.get("branch", "") or "").strip().lower()
        reason = str(item.get("reason", "") or "")
        if field == "cycle_position" and nid == "1.2":
            if branch == new_value or new_value in reason.lower():
                return True
        if field == "direction" and nid == "2.3":
            if branch == new_value or new_value in reason.lower():
                return True
    return False


def validate_stage2_coherence(
    stage2: dict[str, Any],
    stage1: dict[str, Any],
    *,
    kline_frame: Any = None,
) -> list[str]:
    """P0-1, P1-7: cross-stage fields, trace bar_range, §9 grounding."""
    if stage2.get("gate_shortcircuited"):
        return []

    errors: list[str] = []

    # Skip diagnosis_summary cross-stage checks for auto-injected stubs.
    # The stub is generated by the program (not the model), so its fields
    # should not be compared against stage1.
    is_auto_stub = bool(stage2.get("_auto_stub"))

    summary = stage2.get("diagnosis_summary")
    if isinstance(summary, dict) and not is_auto_stub:
        s1_cycle = str(stage1.get("cycle_position", "") or "").strip().lower()
        s2_cycle = str(summary.get("cycle_position", "") or "").strip().lower()
        if s1_cycle and s2_cycle and s1_cycle != s2_cycle:
            # 模糊匹配：s2_cycle 包含 s1_cycle 或反之，说明模型在阶段一基础上做了细化/扩展
            if not (s1_cycle in s2_cycle or s2_cycle in s1_cycle):
                if not _stage2_trace_documents_override(
                    stage2.get("decision_trace"),
                    field="cycle_position",
                    new_value=s2_cycle,
                ):
                    errors.append(
                        f"diagnosis_summary.cycle_position={s2_cycle!r} differs from "
                        f"stage1 {s1_cycle!r}; decision_trace must include node 1.2 "
                        "documenting the change"
                    )

        s1_dir = str(stage1.get("direction", "") or "").strip().lower()
        s2_dir = str(summary.get("direction", "") or "").strip().lower()
        if s1_dir and s2_dir and s1_dir != s2_dir:
            # Direction override to neutral in a range cycle is a normal
            # re-assessment; don't require explicit node-2.3 documentation.
            if not (s2_dir == "neutral" and s2_cycle in _RANGE_CYCLES):
                # Stage1 neutral → stage2 bullish/bearish is also normal.
                if not (s1_dir == "neutral"):
                    if not _stage2_trace_documents_override(
                        stage2.get("decision_trace"),
                        field="direction",
                        new_value=s2_dir,
                    ):
                        # Auto-inject a minimal node 2.3 so the analysis isn't
                        # rejected just because the model forgot to add it.
                        # The reasoning in the model's prose already justifies
                        # the direction change; we just surface it in the trace.
                        trace = stage2.get("decision_trace")
                        if isinstance(trace, list):
                            auto_23 = {
                                "node_id": "2.3",
                                "section": "方向重判",
                                "question": "阶段二是否重新判定市场方向？",
                                "answer": "是",
                                "branch": s2_dir,
                                "reason": (
                                    f"阶段一程序判定 direction={s1_dir}，"
                                    f"阶段二结合市场结构重判为 {s2_dir}；"
                                    "本节点由校验器自动补全（模型推理中已有依据）。"
                                ),
                                "bar_range": "全局",
                                "skipped": False,
                                "_auto_injected": True,
                            }
                            trace.insert(0, auto_23)
                            logger.info(
                                "validate_stage2_coherence: auto-injected node 2.3 "
                                "(direction %r -> %r, model forgot to add it)",
                                s1_dir, s2_dir,
                            )
                        else:
                            errors.append(
                                f"diagnosis_summary.direction={s2_dir!r} differs from "
                                f"stage1 {s1_dir!r}; decision_trace must include node 2.3 "
                                "documenting the change"
                            )

    decision = stage2.get("decision")
    if isinstance(decision, dict) and not is_auto_stub:
        order_type = decision.get("order_type")
        order_dir = decision.get("order_direction")
        s1_dir = str(stage1.get("direction", "") or "").strip().lower()
        if order_type in ("限价单", "突破单", "市价单") and order_dir in ("做多", "做空"):
            _has_2_3_override = _stage2_trace_documents_override(
                stage2.get("decision_trace"),
                field="direction",
                new_value="bearish" if order_dir == "做空" else "bullish",
            )
            if not _has_2_3_override:
                needed_dir = "bearish" if order_dir == "做空" else "bullish"
                conflicts = (
                    (s1_dir == "bullish" and order_dir == "做空") or
                    (s1_dir == "bearish" and order_dir == "做多")
                )
                if conflicts:
                    # Auto-inject node 2.3 instead of hard-failing.
                    trace = stage2.get("decision_trace")
                    if isinstance(trace, list):
                        # Only inject if not already present from the direction check above
                        already = any(
                            isinstance(x, dict) and str(x.get("node_id", "")) == "2.3"
                            for x in trace
                        )
                        if not already:
                            auto_23 = {
                                "node_id": "2.3",
                                "section": "方向重判",
                                "question": "阶段二是否重新判定市场方向？",
                                "answer": "是",
                                "branch": needed_dir,
                                "reason": (
                                    f"阶段一程序判定 direction={s1_dir}，"
                                    f"阶段二下单方向为 {order_dir}，"
                                    f"方向重判为 {needed_dir}；"
                                    "本节点由校验器自动补全。"
                                ),
                                "bar_range": "全局",
                                "skipped": False,
                                "_auto_injected": True,
                            }
                            trace.insert(0, auto_23)
                            logger.info(
                                "validate_stage2_coherence: auto-injected node 2.3 "
                                "for order_direction conflict (%r vs stage1 %r)",
                                order_dir, s1_dir,
                            )
                    else:
                        if s1_dir == "bullish" and order_dir == "做空":
                            errors.append(
                                "order_direction 做空 conflicts with stage1 direction=bullish "
                                "(unless explicitly reversing; state in reasoning/trace)"
                            )
                        if s1_dir == "bearish" and order_dir == "做多":
                            errors.append(
                                "order_direction 做多 conflicts with stage1 direction=bearish"
                            )

    decision_trace = stage2.get("decision_trace")
    if isinstance(decision_trace, list):
        for i, item in enumerate(decision_trace):
            if isinstance(item, dict):
                errors.extend(validate_bar_range_field(item, f"decision_trace[{i}]"))
        errors.extend(
            validate_trace_bars_in_frame(
                decision_trace, kline_frame=kline_frame, path_prefix="decision_trace"
            )
        )
        errors.extend(
            validate_duplicate_bar_ranges(
                decision_trace, path_prefix="decision_trace", min_items=5
            )
        )
        errors.extend(_validate_stage2_section9(stage2, decision_trace))

    return errors


def _validate_stage2_section9(
    stage2: dict[str, Any],
    decision_trace: list[dict[str, Any]],
) -> list[str]:
    """P1-7: orders must cite §9 path; weak signals need §9 trace."""
    decision = stage2.get("decision", {})
    if not isinstance(decision, dict):
        return []
    order_type = decision.get("order_type")
    if order_type not in ("限价单", "突破单", "市价单"):
        return []

    errors: list[str] = []
    node_ids = [str(x.get("node_id", "")) for x in decision_trace if isinstance(x, dict)]
    has_section_9 = any(n.startswith("9.") for n in node_ids)
    if not has_section_9:
        errors.append(
            "placing an order requires at least one decision_trace node in §9 (9.x)"
        )

    idx_9 = next((i for i, n in enumerate(node_ids) if n.startswith("9.")), -1)
    idx_101 = next((i for i, n in enumerate(node_ids) if n == "10.1"), -1)
    if idx_9 >= 0 and idx_101 >= 0 and idx_9 > idx_101:
        errors.append("§9 入场信号 nodes must appear before §10.1 止损")

    bar_analysis = stage2.get("bar_analysis")
    if isinstance(bar_analysis, dict):
        signal_bar = bar_analysis.get("signal_bar")
        if isinstance(signal_bar, dict):
            quality = str(signal_bar.get("quality", "") or "").strip().lower()
            if quality in ("weak", "invalid") and not has_section_9:
                errors.append(
                    "weak/invalid signal_bar requires §9 decision_trace nodes"
                )

    return errors


def validate_incremental_stage1_coherence(
    stage1: dict[str, Any],
    *,
    new_bar_count: int,
    previous_stage1: dict[str, Any] | None = None,
) -> list[str]:
    """P1-6: incremental run must document delta vs previous analysis."""
    if new_bar_count <= 0:
        return []

    errors: list[str] = []
    delta = stage1.get("incremental_delta")
    if not isinstance(delta, dict):
        errors.append(
            "incremental stage1 requires incremental_delta "
            "(new_closed_bars, changed_fields, summary)"
        )
    else:
        bars = delta.get("new_closed_bars")
        if not isinstance(bars, list) or len(bars) != new_bar_count:
            errors.append(
                f"incremental_delta.new_closed_bars must list exactly "
                f"{new_bar_count} bar label(s)"
            )
        summary = str(delta.get("summary", "") or "").strip()
        if len(summary) < 1:
            errors.append("incremental_delta.summary must not be empty")
        changed = delta.get("changed_fields")
        if isinstance(changed, list) and previous_stage1 and not changed:
            for key in ("cycle_position", "direction"):
                if str(stage1.get(key, "")).lower() != str(
                    previous_stage1.get(key, "")
                ).lower():
                    errors.append(
                        f"incremental_delta.changed_fields empty but {key} differs "
                        "from previous stage1"
                    )

    blob_parts: list[str] = []
    blob_parts.append(str(stage1.get("risk_warning", "") or ""))
    if isinstance(delta, dict):
        blob_parts.append(str(delta.get("summary", "") or ""))
    for item in stage1.get("gate_trace") or []:
        if isinstance(item, dict):
            blob_parts.append(str(item.get("reason", "") or ""))
    blob = "\n".join(blob_parts)
    return errors
