"""Normalize common Stage 1 AI JSON variants before schema validation."""
from __future__ import annotations

import copy
import logging
import re
from typing import Any

from pa_agent.ai.coherence_checks import BAR_BY_BAR_TARGET_COUNT
from pa_agent.ai.trace_normalize import normalize_stage1_traces

logger = logging.getLogger(__name__)

# Common model aliases for on-disk strategy file names.
_STRATEGY_FILE_ALIASES: dict[str, str] = {
    "交易区间分析识别.txt": "震荡区间分析识别.txt",
    "交易区间交易策略.txt": "震荡区间交易策略.txt",
    "宽通道分析识别.txt": "文件13-窄通道与宽通道策略.txt",
    "宽通道交易策略.txt": "文件13-窄通道与宽通道策略.txt",
    "下跌通道策略.txt": "下跌通道交易策略.txt",
    "下跌通道策略": "下跌通道交易策略.txt",
}

_BAR_ROLE_ALIASES: dict[str, str] = {
    "reversal_attempt": "signal",
    "reversal": "signal",
    "continue": "confirmation",
    "continued": "confirmation",
    "continuation": "confirmation",
    "follow": "confirmation",
    "follow_through": "confirmation",
    "follow-through": "confirmation",
    "confirm": "confirmation",
    "confirmed": "confirmation",
    "breakout": "signal",
    "setup": "signal",
    "pullback": "test",
    "retest": "test",
    "breakout_test": "test",
    "breakout_pullback": "test",
    "breakout-test": "test",
    "breakout_pullback_test": "test",
    "support": "structure",
    "resistance": "structure",
    "failure": "trap",
    "failed": "trap",
    "exhaustion": "climax",
    "trend": "trend_bull",  # ambiguous → default bullish
    "趋势阳线": "trend_bull",
    "趋势阴线": "trend_bear",
    "延续": "confirmation",
    "跟随": "confirmation",
    "确认": "confirmation",
    "结构": "structure",
    "信号": "signal",
    "入场": "entry",
    "噪音": "noise",
    "噪声": "noise",
    "陷阱": "trap",
    "高潮": "climax",
    "测试": "test",
    "transition": "structure",
    "transitional": "structure",
    "过渡": "structure",
}

# Model often omits the trailing "s" on strengthens_* / weakens_*.
# Model often uses "low" as a synonym for "weak" in signal_bar.quality.
_SIGNAL_BAR_QUALITY_ALIASES: dict[str, str] = {
    "low": "weak",
    "high": "strong",
    "moderate": "medium",
    "poor": "weak",
    "good": "strong",
    "bad": "invalid",
    # "valid" means "signal meets criteria" but is not a quality descriptor
    "valid": "medium",
    "invalid": "invalid",
    # 中文 synonyms
    "弱": "weak",
    "中": "medium",
    "强": "strong",
    "无效": "invalid",
    "有效": "medium",
}

# Model often uses "moderate" as a synonym for "medium" in transition_risk.
_TRANSITION_RISK_ALIASES: dict[str, str] = {
    "moderate": "medium",
    "moderately_high": "high",
    "moderately_low": "low",
    "moderate_high": "high",
    "moderate_low": "low",
    "mid": "medium",
}


_CONTEXT_EFFECT_ALIASES: dict[str, str] = {
    "strengthen_bull": "strengthens_bull",
    "strengthen_bear": "strengthens_bear",
    "strengthens_bull": "strengthens_bull",
    "strengthens_bear": "strengthens_bear",
    "strengthens_bulls": "strengthens_bull",   # AI typo: extra 's'
    "strengthens_bears": "strengthens_bear",   # AI typo: extra 's'
    "strengthens_bash": "strengthens_bear",    # AI typo: bash → bear
    "strengthen_bash": "strengthens_bear",
    "weakens_bull": "weakens_bull",
    "weakens_bear": "weakens_bear",
    "weaken_bull": "weakens_bull",
    "weaken_bear": "weakens_bear",
    "weakened_bull": "weakened_bull",
    "weakened_bear": "weakened_bear",
    "weakens_bulls": "weakens_bull",           # AI typo: extra 's'
    "weakens_bears": "weakens_bear",           # AI typo: extra 's'
    "neutral": "neutral",
    "transition": "transition",
}

_BAR_TYPE_ENUM = frozenset({
    "trend_bull", "trend_bear", "doji", "inside",
    "outside_bull", "outside_bear", "flat", "other",
})
_VALID_BAR_ROLES = frozenset({
    "structure", "signal", "entry", "confirmation", "noise", "trap", "climax", "test",
})

_VALID_CONTEXT_EFFECTS = frozenset({
    "strengthens_bull", "weakens_bull", "strengthens_bear", "weakens_bear",
    "neutral", "transition", "weakened_bull", "weakened_bear",
})

_BAR_TYPE_ALIASES: dict[str, str] = {
    "ine": "inside",
    "ins": "inside",
    "insid": "inside",
    "doj": "doji",
    "trendbull": "trend_bull",
    "trendbear": "trend_bear",
    "outsidebull": "outside_bull",
    "outsidebear": "outside_bear",
}


def _strip_enum_suffix(raw: str) -> str:
    text = raw.strip()
    for sep in ("（", "(", "【", "[", "—", "–", " - ", "：", ":"):
        if sep in text:
            head = text.split(sep, 1)[0].strip()
            if head:
                return head
    return text


def _normalize_bar_type_value(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    text = _strip_enum_suffix(raw)
    key = text.strip().lower().replace(" ", "_")
    key = _BAR_TYPE_ALIASES.get(key, key)
    if key in _BAR_TYPE_ENUM:
        return key
    for token in sorted(_BAR_TYPE_ENUM, key=len, reverse=True):
        if key.startswith(token) or token.startswith(key):
            return token
    return None


def _bar_type_from_summary(out: dict[str, Any], bar_label: str) -> str | None:
    summary = out.get("bar_by_bar_summary")
    if not isinstance(summary, list):
        return None
    target = str(bar_label or "K1").strip().upper()
    for item in summary:
        if not isinstance(item, dict):
            continue
        if str(item.get("bar", "")).strip().upper() != target:
            continue
        return _normalize_bar_type_value(item.get("bar_type"))
    return None


def _normalize_bar_types(out: dict[str, Any]) -> None:
    """Fix truncated bar_type tokens (e.g. inside→ine) before schema validation."""
    summary = out.get("bar_by_bar_summary")
    if isinstance(summary, list):
        for item in summary:
            if not isinstance(item, dict):
                continue
            raw = item.get("bar_type")
            norm = _normalize_bar_type_value(raw)
            if norm and norm != raw:
                item["bar_type"] = norm
                logger.debug("Mapped bar_by_bar_summary bar_type %r -> %s", raw, norm)

    bar_analysis = out.get("bar_analysis")
    if not isinstance(bar_analysis, dict):
        return
    raw_bt = bar_analysis.get("bar_type")
    norm_bt = _normalize_bar_type_value(raw_bt)
    if norm_bt is None:
        last_bar = str(bar_analysis.get("last_closed_bar") or "K1")
        norm_bt = _bar_type_from_summary(out, last_bar)
    if norm_bt and norm_bt != raw_bt:
        bar_analysis["bar_type"] = norm_bt
        logger.debug("Mapped bar_analysis.bar_type %r -> %s", raw_bt, norm_bt)


def _hoist_bar_by_bar_summary(out: dict[str, Any]) -> None:
    """Move bar_by_bar_summary from bar_analysis to root when the model nests it."""
    root = out.get("bar_by_bar_summary")
    if isinstance(root, list) and root:
        return
    ba = out.get("bar_analysis")
    if not isinstance(ba, dict):
        return
    nested = ba.get("bar_by_bar_summary")
    if not isinstance(nested, list) or not nested:
        return
    out["bar_by_bar_summary"] = nested
    ba.pop("bar_by_bar_summary", None)
    logger.debug("Hoisted bar_by_bar_summary from bar_analysis to root (%s items)", len(nested))


def _normalize_strategy_file_names(files: Any) -> list[str]:
    if not isinstance(files, list):
        return []
    out: list[str] = []
    for item in files:
        if not isinstance(item, str):
            continue
        name = _STRATEGY_FILE_ALIASES.get(item.strip(), item.strip())
        if name and name not in out:
            out.append(name)
    return out


def _normalize_bar_role_value(raw: str) -> str | None:
    key = raw.strip().lower().replace(" ", "_").replace("-", "_")
    mapped = _BAR_ROLE_ALIASES.get(key)
    if mapped:
        return mapped
    if key in _VALID_BAR_ROLES:
        return key
    if "breakout" in key and ("test" in key or "pullback" in key):
        return "test"
    if key.startswith("trend_"):
        return "structure"
    return None


def _normalize_context_effect_value(raw: str) -> str | None:
    key = raw.strip().lower().replace(" ", "_").replace("-", "_")
    mapped = _CONTEXT_EFFECT_ALIASES.get(key)
    if mapped:
        return mapped
    if key in _VALID_CONTEXT_EFFECTS:
        return key
    if key.startswith("strengthen"):
        if "bear" in key or "bash" in key:
            return "strengthens_bear"
        if "bull" in key:
            return "strengthens_bull"
    if key.startswith("weaken"):
        if "bear" in key:
            return "weakens_bear"
        if "bull" in key:
            return "weakens_bull"
    return None


def _normalize_bar_by_bar_roles(out: dict[str, Any]) -> None:
    summary = out.get("bar_by_bar_summary")
    if not isinstance(summary, list):
        return
    for item in summary:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if not isinstance(role, str):
            continue
        normalized = _normalize_bar_role_value(role)
        if normalized and normalized != role:
            item["role"] = normalized
            logger.debug("Mapped bar_by_bar_summary role %r -> %s", role, normalized)


def _normalize_bar_by_bar_context_effects(out: dict[str, Any]) -> None:
    summary = out.get("bar_by_bar_summary")
    if not isinstance(summary, list):
        return
    for item in summary:
        if not isinstance(item, dict):
            continue
        effect = item.get("context_effect")
        if not isinstance(effect, str):
            continue
        normalized = _normalize_context_effect_value(effect)
        if normalized and normalized != effect:
            item["context_effect"] = normalized
            logger.debug(
                "Mapped bar_by_bar_summary context_effect %r -> %s",
                effect,
                normalized,
            )


_VALID_TRAPPED_SIDES = frozenset({"bulls", "bears", "both", "none", "unknown"})


def _normalize_bar_by_bar_trapped_side(out: dict[str, Any]) -> None:
    summary = out.get("bar_by_bar_summary")
    if not isinstance(summary, list):
        return
    for item in summary:
        if not isinstance(item, dict):
            continue
        side = item.get("trapped_side")
        if side is None or (isinstance(side, str) and not side.strip()):
            item["trapped_side"] = "none"
            logger.debug("Mapped bar_by_bar_summary trapped_side null/empty -> none")
            continue
        if isinstance(side, str):
            key = side.strip().lower()
            if key in _VALID_TRAPPED_SIDES:
                if key != side:
                    item["trapped_side"] = key
            else:
                item["trapped_side"] = "unknown"
                logger.debug("Mapped bar_by_bar_summary trapped_side %r -> unknown", side)


def _infer_signal_bar_from_summary(summary: object) -> dict[str, Any] | None:
    """Build signal_bar from the newest bar_by_bar_summary item with role=signal."""
    if not isinstance(summary, list):
        return None
    for item in summary:
        if not isinstance(item, dict):
            continue
        if str(item.get("role", "")).strip().lower() != "signal":
            continue
        bar_type = str(item.get("bar_type", "")).strip().lower()
        if bar_type in ("trend_bull", "trend_bear"):
            quality = "strong"
        elif bar_type in ("outside_bull", "outside_bear"):
            quality = "medium"
        elif bar_type in ("doji", "inside", "flat", "other"):
            quality = "weak"
        else:
            quality = "invalid"
        reason = str(item.get("reason", "") or "").strip()
        if not reason:
            reason = "从 bar_by_bar_summary（role=signal）推断"
        return {
            "bar": item.get("bar"),
            "quality": quality,
            "reason": reason,
        }
    return None


def _normalize_signal_bar_object(out: dict[str, Any]) -> bool:
    """``signal_bar`` must be an object; models often emit null when signal is in summary."""
    bar_analysis = out.get("bar_analysis")
    if not isinstance(bar_analysis, dict):
        return False

    signal_bar = bar_analysis.get("signal_bar")
    if isinstance(signal_bar, dict):
        if not str(signal_bar.get("reason", "") or "").strip():
            signal_bar["reason"] = "见 bar_by_bar_summary"
        if "bar" not in signal_bar:
            signal_bar["bar"] = None
        q = signal_bar.get("quality")
        if q is None or not isinstance(q, str) or not str(q).strip():
            # setdefault skips explicit null; schema requires enum string
            signal_bar["quality"] = "invalid" if signal_bar.get("bar") is None else "weak"
        return False

    inferred = _infer_signal_bar_from_summary(out.get("bar_by_bar_summary"))
    if inferred is None:
        last_bar = str(bar_analysis.get("last_closed_bar", "K1") or "K1").strip()
        bar_type = str(bar_analysis.get("bar_type", "") or "").strip().lower()
        quality = "weak" if bar_type in ("doji", "inside", "flat", "other") else "invalid"
        inferred = {
            "bar": None,
            "quality": quality,
            "reason": (
                f"模型 signal_bar=null；最近收盘棒 {last_bar}（{bar_type or 'unknown'}），"
                "无独立已确认信号棒，见 bar_by_bar_summary"
            ),
        }
    bar_analysis["signal_bar"] = inferred
    logger.debug(
        "Normalized bar_analysis.signal_bar null -> %r",
        inferred.get("bar"),
    )
    return True


def _normalize_signal_bar_quality(out: dict[str, Any]) -> None:
    """Normalize signal_bar.quality to valid enum values."""
    bar_analysis = out.get("bar_analysis")
    if not isinstance(bar_analysis, dict):
        return
    signal_bar = bar_analysis.get("signal_bar")
    if not isinstance(signal_bar, dict):
        return
    quality = signal_bar.get("quality")
    if quality is None or not isinstance(quality, str) or not str(quality).strip():
        signal_bar["quality"] = "invalid" if signal_bar.get("bar") is None else "weak"
        return
    key = quality.strip().lower()
    normalized = _SIGNAL_BAR_QUALITY_ALIASES.get(key)
    if normalized and normalized != quality:
        signal_bar["quality"] = normalized
        logger.debug("Mapped signal_bar.quality %r -> %s", quality, normalized)


def _normalize_transition_risk(out: dict[str, Any]) -> None:
    """Normalize transition_risk to valid enum values."""
    risk = out.get("transition_risk")
    if not isinstance(risk, str):
        return
    key = risk.strip().lower()
    normalized = _TRANSITION_RISK_ALIASES.get(key)
    if normalized and normalized != risk:
        out["transition_risk"] = normalized
        logger.debug("Mapped transition_risk %r -> %s", risk, normalized)


def _summary_bar_seq(bar_label: object) -> int | None:
    m = re.search(r"K\s*(\d+)", str(bar_label or ""), re.IGNORECASE)
    return int(m.group(1)) if m else None


def _pad_bar_by_bar_summary_to_minimum(
    out: dict[str, Any],
    *,
    kline_frame: Any = None,
) -> None:
    """Pad bar_by_bar_summary to min(BAR_BY_BAR_TARGET_COUNT, n_bars) using geometry stubs."""
    summary = out.get("bar_by_bar_summary")
    if not isinstance(summary, list) or kline_frame is None:
        return

    bars = getattr(kline_frame, "bars", None) or ()
    seqs = [int(getattr(b, "seq", 0)) for b in bars if getattr(b, "seq", None)]
    n_bars = max(seqs) if seqs else 0
    if n_bars < 1:
        return

    target = BAR_BY_BAR_TARGET_COUNT
    expected_min = min(target, n_bars) if n_bars >= target else n_bars
    if len(summary) >= expected_min:
        return

    present = {
        seq
        for item in summary
        if isinstance(item, dict)
        for seq in (_summary_bar_seq(item.get("bar")),)
        if seq is not None
    }
    missing = [seq for seq in range(expected_min, 0, -1) if seq not in present]
    if not missing:
        return

    from pa_agent.ai.kline_features import compute_kline_geometry_features

    features = {f.seq: f for f in compute_kline_geometry_features(kline_frame, limit=12)}
    padded: list[dict[str, Any]] = []
    for seq in missing:
        feat = features.get(seq)
        bar_type = feat.bar_type if feat else "doji"
        padded.append(
            {
                "bar": f"K{seq}",
                "role": "structure",
                "bar_type": bar_type,
                "context_effect": "neutral",
                "follow_through": "pending" if seq == 1 else "no",
                "trapped_side": "none",
                "reason": (
                    f"程序补全K{seq}（模型仅写了{len(summary)}条摘要，窗口需至少{expected_min}根）；"
                    f"几何分类={bar_type}，细节见K线几何特征表。"
                ),
            }
        )

    merged = padded + [x for x in summary if isinstance(x, dict)]
    merged.sort(key=lambda x: _summary_bar_seq(x.get("bar")) or 0, reverse=True)
    out["bar_by_bar_summary"] = merged
    logger.debug(
        "bar_by_bar_summary padded %s -> %s items (expected_min=%s)",
        len(summary),
        len(merged),
        expected_min,
    )


# Pattern-name values the model occasionally places in cycle_position instead of
# detected_patterns.  Each entry maps the invalid cp string →
#   (pattern_tag_to_rescue, fallback_cycle_position).
# The fallback is the most natural host structure for that formation.
_PATTERN_MISPLACED_AS_CYCLE: dict[str, tuple[str, str]] = {
    "ascending_triangle":   ("ascending_triangle",   "trading_range"),
    "descending_triangle":  ("descending_triangle",  "trading_range"),
    "symmetrical_triangle": ("symmetrical_triangle", "trading_range"),
    "expanding_triangle":   ("expanding_triangle",   "trading_range"),
    "wedge":                ("wedge",                "broad_channel"),
    "double_top":           ("double_top_bottom",    "trading_range"),
    "double_bottom":        ("double_top_bottom",    "trading_range"),
    "double_top_bottom":    ("double_top_bottom",    "trading_range"),
    "mtr":                  ("mtr",                  "broad_channel"),
    "final_flag":           ("final_flag",           "broad_channel"),
    "breakout_failure":     ("breakout_failure",     "trading_range"),
    "failed_breakout":      ("breakout_failure",     "trading_range"),
    "breakout_test":        ("breakout_test",        "trading_range"),
    "barbwire":             ("barbwire",             "trading_range"),
}

_VALID_CYCLE_POSITIONS: frozenset[str] = frozenset([
    "spike", "micro_channel", "tight_channel", "normal_channel", "broad_channel",
    "trending_tr", "trading_range", "extreme_tr", "unknown",
])


def _rescue_pattern_from_cycle_position(out: dict[str, Any]) -> bool:
    """Fix the model putting a pattern name (e.g. 'descending_triangle') in cycle_position.

    When detected, the pattern tag is injected into detected_patterns and cycle_position
    is replaced with the most natural fallback host state.  Returns True if a rescue was
    performed.
    """
    cp = str(out.get("cycle_position", "") or "").strip().lower()
    if cp in _VALID_CYCLE_POSITIONS:
        return False  # already valid — nothing to do

    entry = _PATTERN_MISPLACED_AS_CYCLE.get(cp)
    if entry is None:
        # Unknown value but not a known pattern name: leave it for the validator to reject.
        return False

    pattern_tag, fallback_cp = entry

    # Inject the rescued pattern tag into detected_patterns.
    patterns: list[str] = list(out.get("detected_patterns") or [])
    if pattern_tag not in patterns:
        patterns.insert(0, pattern_tag)
        out["detected_patterns"] = patterns

    logger.warning(
        "cycle_position %r is a pattern name, not a market state — "
        "rescued tag %r into detected_patterns and replaced cycle_position with %r",
        cp,
        pattern_tag,
        fallback_cp,
    )
    out["cycle_position"] = fallback_cp
    return True


_INCREMENTAL_TRACKED_FIELDS = (
    "cycle_position",
    "alternative_cycle_position",
    "direction",
    "diagnosis_confidence",
    "market_phase",
    "transition_risk",
    "gate_result",
    "entry_setup",
    "spike_stage",
    "support_levels",
    "resistance_levels",
)


def _incremental_summary_from_risk_warning(risk_warning: str) -> str | None:
    text = (risk_warning or "").strip()
    if not text:
        return None
    for marker in ("相对上一轮", "相对上轮", "本轮", "新增K"):
        idx = text.find(marker)
        if idx >= 0:
            chunk = text[idx:].split("。", 1)[0].strip()
            if len(chunk) >= 1:
                return chunk + ("。" if not chunk.endswith("。") else "")
    return None


def _fill_incremental_delta(
    out: dict[str, Any],
    *,
    new_bar_count: int,
    previous_stage1: dict[str, Any] | None = None,
) -> None:
    """Synthesize incremental_delta when the model outputs a full stage1 JSON without it."""
    if new_bar_count <= 0:
        return

    delta = out.get("incremental_delta")
    if not isinstance(delta, dict):
        delta = {}
        out["incremental_delta"] = delta

    expected_bars = [f"K{i}" for i in range(1, new_bar_count + 1)]
    bars = delta.get("new_closed_bars")
    if not isinstance(bars, list) or len(bars) != new_bar_count:
        delta["new_closed_bars"] = expected_bars
        logger.debug(
            "incremental_delta.new_closed_bars filled -> %s",
            expected_bars,
        )

    summary = str(delta.get("summary", "") or "").strip()
    if len(summary) < 1:
        from_rw = _incremental_summary_from_risk_warning(
            str(out.get("risk_warning", "") or "")
        )
        if from_rw:
            delta["summary"] = from_rw
        else:
            delta["summary"] = (
                f"本轮新增{new_bar_count}根已收盘K线（{', '.join(expected_bars)}），"
                "已结合完整K线窗口更新阶段一诊断与闸门判断。"
            )
        logger.debug("incremental_delta.summary synthesized")

    changed = delta.get("changed_fields")
    if not isinstance(changed, list):
        changed = []
        delta["changed_fields"] = changed
    if previous_stage1 and not changed:
        for key in _INCREMENTAL_TRACKED_FIELDS:
            cur = str(out.get(key, "") or "").strip().lower()
            prev = str(previous_stage1.get(key, "") or "").strip().lower()
            if cur != prev:
                changed.append(key)


def normalize_stage1(
    obj: dict[str, Any],
    *,
    normalization_mode: str = "strict",
    kline_frame: Any = None,
    incremental_new_bar_count: int = 0,
    incremental_previous_stage1: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy of *obj* with known AI quirks corrected."""
    out = copy.deepcopy(obj)

    # ── Unwrap nested wrapper: {"meta": {...}, "stage1_diagnosis": { <actual> }} ──
    # Models occasionally wrap the diagnosis inside a "stage1_diagnosis" key,
    # or include extra top-level metadata fields alongside the diagnosis.
    if "stage1_diagnosis" in out and isinstance(out["stage1_diagnosis"], dict):
        inner = out["stage1_diagnosis"]
        # Only unwrap if the inner dict has core diagnosis fields and the outer doesn't
        if "cycle_position" in inner and "cycle_position" not in out:
            # Merge inner into out, preserving any incremental_delta that may be at top level
            delta_top = out.get("incremental_delta")
            out = inner
            if delta_top is not None and "incremental_delta" not in out:
                out["incremental_delta"] = delta_top
            logger.debug("Unwrapped stage1_diagnosis nested wrapper")

    lenient = normalization_mode == "lenient"

    # ── Rescue pattern names misplaced in cycle_position ─────────────────────
    _rescue_pattern_from_cycle_position(out)

    # ── DecisionNodeEngine: fill §1.1/§2.3/§2.4 (before strategy_files routing) ──
    if kline_frame is not None:
        try:
            from pa_agent.ai.decision_nodes import DecisionNodeEngine
            DecisionNodeEngine.apply_stage1(out, kline_frame)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DecisionNodeEngine.apply_stage1 failed: %s", exc)

    if "strategy_files_needed" not in out or out.get("strategy_files_needed") is None:
        alt = out.pop("recommended_strategy_files", None)
        if alt is not None:
            out["strategy_files_needed"] = _normalize_strategy_file_names(alt)
            logger.debug("Mapped recommended_strategy_files -> strategy_files_needed")
        elif out.get("cycle_position") and out.get("direction"):
            try:
                from pa_agent.ai.router import route_strategy_files

                out["strategy_files_needed"] = route_strategy_files(out)
                logger.debug("Filled strategy_files_needed from router")
            except Exception as exc:  # noqa: BLE001
                logger.debug("router fallback for strategy_files_needed failed: %s", exc)
                out.setdefault("strategy_files_needed", [])
    else:
        out["strategy_files_needed"] = _normalize_strategy_file_names(
            out.get("strategy_files_needed")
        )

    from pa_agent.ai.pattern_routing import ensure_detected_patterns_coherent

    if kline_frame is not None:
        try:
            from pa_agent.ai.market_features import build_program_features_dict

            out["program_features"] = build_program_features_dict(kline_frame)
        except Exception as exc:  # noqa: BLE001
            logger.warning("build_program_features_dict failed: %s", exc)

    ensure_detected_patterns_coherent(out, kline_frame=kline_frame)

    if not out.get("climax_risk"):
        patterns = out.get("detected_patterns") or []
        if isinstance(patterns, list):
            if "climax_triggered" in patterns:
                out["climax_risk"] = "triggered"
            elif "climax_warning" in patterns:
                out["climax_risk"] = "warning"
        if not out.get("climax_risk"):
            out["climax_risk"] = "none"

    _hoist_bar_by_bar_summary(out)
    normalize_stage1_traces(out, normalization_mode=normalization_mode)
    _normalize_bar_by_bar_roles(out)
    _normalize_bar_by_bar_context_effects(out)
    _normalize_bar_by_bar_trapped_side(out)
    _normalize_bar_types(out)
    _normalize_signal_bar_object(out)
    _normalize_signal_bar_quality(out)
    _normalize_transition_risk(out)
    _pad_bar_by_bar_summary_to_minimum(out, kline_frame=kline_frame)
    _fill_incremental_delta(
        out,
        new_bar_count=incremental_new_bar_count,
        previous_stage1=incremental_previous_stage1,
    )

    if kline_frame is not None:
        try:
            from pa_agent.ai.structure_levels import refresh_stage1_support_resistance

            refresh_stage1_support_resistance(out, kline_frame)
        except Exception as exc:  # noqa: BLE001
            logger.warning("refresh_stage1_support_resistance failed: %s", exc)

    return out
