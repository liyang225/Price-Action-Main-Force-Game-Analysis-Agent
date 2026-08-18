"""Stage-1 pattern tags, entry_setup overlays, and stage-1 briefs for routing."""
from __future__ import annotations

import re
from typing import Any

# H1/H2/L1/L2 count setup — avoid bare「计数」(e.g. EMA缺口计数) false positives.
_HL_COUNT_SETUP_RE = re.compile(
    r"(?<![a-z])(?:h[12]|l[12])(?![a-z])|计数入场|high\s*[12]|low\s*[12]",
    re.IGNORECASE,
)

# Maps bar_analysis.entry_setup_type -> detected_patterns keys to merge before routing.
ENTRY_SETUP_TYPE_PATTERN_OVERLAY: dict[str, tuple[str, ...]] = {
    "wedge": ("wedge",),
    "breakout_pullback": ("breakout_pullback",),
    "mtr": ("mtr", "reversal_attempt"),
    "h1": ("h1",),
    "h2": ("h2", "reversal_attempt"),
    "l1": ("l1",),
    "l2": ("l2", "reversal_attempt"),
    "tr_boundary": ("middle_range", "barbwire"),
}

# Keywords in key_signals / risk_warning → detected_patterns tag (sync + post-sync coherence).
_PATTERN_KEYWORD_TAGS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("楔形", "三推", "三推动", "wedge"), "wedge"),
    (("突破测试", "突破回踩", "失败的失败"), "breakout_test"),
    (("假突破", "突破失败", "failed breakout", "突破后快速收复"), "breakout_failure"),
    (("mtr", "主要趋势反转", "趋势反转尝试"), "mtr"),
    (("铁丝网", "barbwire", "凝滞区"), "barbwire"),
    (("重叠度高", "重叠多", "k线重叠", "重叠严重"), "overlap"),
    (("区间下沿", "区间上沿", "区间边界", "交易区间", "交易区间下沿", "交易区间上沿"), "middle_range"),
    (("h1", "h2", "l1", "l2", "计数入场", "high1", "high2", "low1", "low2"), "h1"),
    (("always in", "ail", "ais", "20gb", "缺口棒"), "always_in"),
    (("磁力", "套住", "trapped", "信号失败"), "failed_signal"),
    (("双顶", "双底", "双重顶", "双重底", "double top", "double bottom"), "double_top_bottom"),
    (("上升三角形",), "ascending_triangle"),
    (("下降三角形",), "descending_triangle"),
    (("对称三角形",), "symmetrical_triangle"),
    (("扩张三角形",), "expanding_triangle"),
    (("最终旗形", "final flag", "ffes"), "final_flag"),
)

_CYCLE_POSITION_PATTERN_TAGS: dict[str, tuple[str, ...]] = {
    "trading_range": ("middle_range", "barbwire", "overlap"),
    "trending_tr": ("middle_range", "overlap"),
}

_HL_PATTERN_KEYWORDS: dict[str, str] = {
    "h1": "h1",
    "h2": "h2",
    "l1": "l1",
    "l2": "l2",
}

STAGE1_DETECTED_PATTERNS_GUIDE = """
## detected_patterns 判定表（阶段一必填标签）

在 `detected_patterns` 中填写**英文 key**（可多个）。程序据此在阶段二加载对应策略文件。
若 `bar_analysis.entry_setup_type` 已判定为 wedge / breakout_pullback 等，**必须**在 `detected_patterns` 中写入对应 key（程序也会从 entry_setup_type 补全路由，但字段仍须一致）。

| key | 何时填写 | 阶段二加载 |
|-----|----------|------------|
| wedge | 三次同向推进、幅度递减、趋势线/通道收敛；含楔形回撤与楔形反转 | 文件14-楔形形态分析交易.txt |
| reversal_attempt | 反转尝试、MTR 前后结构、final flag、明显二次测试失败 | 文件15-二次入场机会.txt |
| mtr | 主要趋势反转结构已成型（常与 reversal_attempt 同现） | 文件15（叠加）、文件25 |
| final_flag | 趋势末段 final flag / 末端旗形 | 文件15（叠加）、文件24 |
| h1 / h2 / l1 / l2 | 计数入场结构（High1/High2/Low1/Low2） | 文件19-H1H2-L1L2计数.txt |
| breakout_test | 突破后回测突破位、突破测试棒 | 文件18 |
| breakout_pullback | 突破失败后的再次失败（突破回踩）顺势机会 | 文件18 |
| breakout_failure / failed_breakout | 普通突破失败、假突破 | 文件18、文件22 |
| always_in / ail / ais / 20gb / gap_bar | Always In、20GB、缺口棒等 | 文件20-AlwaysIn与20GB.txt |
| barbwire / wire / overlap / middle_range | 铁丝网、重叠、区间中部 | 文件21-铁丝网与无交易环境.txt |
| failed_signal / magnet / trapped_traders | 信号失败后磁力位、交易者被套 | 文件22-信号失败后的磁力位.txt |
| ascending_triangle / descending_triangle / symmetrical_triangle / expanding_triangle | 三角形收敛形态 | 文件27-三角形与收敛形态.txt |
| double_top_bottom | 双顶、双底、微型双顶底 | 文件28-双重顶底与微型结构.txt |

阶段二常驻（非 pattern 触发）：文件23（Measured Move）。

**与 entry_setup_type 对齐：**
- entry_setup_type=wedge → detected_patterns 须含 wedge
- entry_setup_type=breakout_pullback → 须含 breakout_pullback
- entry_setup_type=MTR → 建议含 mtr 与 reversal_attempt
- entry_setup_type=H1/H2/L1/L2 → 须含对应 h1/h2/l1/l2
- entry_setup_type=tr_boundary → 须含 middle_range 与 barbwire（区间边界/铁丝网）；程序会自动补全

无特殊形态时填 `[]`。勿把形态只写在 key_signals 而不写 detected_patterns（程序会按 entry_setup_type 与关键词尝试补全 detected_patterns）。
""".strip()

STAGE1_PATTERN_BRIEFS_BLOCK = """
## 特殊形态阶段一速查（保守模式：判定要点；细则在阶段二 playbook）

**wedge**：三推同向、每推幅度递减、两线收敛；上升楔形偏看跌突破、下降楔形偏看涨突破。
**breakout_test / breakout_pullback**：突破后回测突破位；「失败的失败」= 突破回踩顺势。
**breakout_failure**：突破后无跟随、快速回到结构内。
**reversal_attempt / mtr**：逆主趋势反转尝试；等待二次入场优于第一次。
**h1/h2/l1/l2**：计数入场；h2/l2 二次入场胜率通常更高。
**barbwire / overlap / middle_range**：铁丝网、重叠、区间中部或边界；entry_setup_type=tr_boundary 时两者均应写入 detected_patterns。
**always_in / 20gb**：强趋势连续同向棒；逆势需双确认。
**failed_signal / magnet**：信号棒失败后价格被吸向磁力位。
**final_flag / mtr**：最终旗形、主要趋势反转见文件24/25。
**double_top_bottom / 三角形**：见文件28/27。
""".strip()


def _barbwire_blocked(stage1_json: dict[str, Any]) -> bool:
    """True when program_features explicitly forbids barbwire auto-tagging."""
    pf = stage1_json.get("program_features")
    if isinstance(pf, dict) and pf.get("barbwire_candidate") is False:
        return True
    return False


def _barbwire_candidate(stage1_json: dict[str, Any], kline_frame: Any = None) -> bool:
    if _barbwire_blocked(stage1_json):
        return False
    pf = stage1_json.get("program_features")
    if isinstance(pf, dict) and "barbwire_candidate" in pf:
        return bool(pf["barbwire_candidate"])
    if kline_frame is not None:
        try:
            from pa_agent.ai.market_features import compute_simple_market_features

            return compute_simple_market_features(kline_frame).barbwire_candidate
        except Exception:
            pass
    return False


def _barbwire_keyword_negated(text: str) -> bool:
    """Skip keyword sync when narrative discusses sub-threshold barbwire score."""
    lowered = text.lower()
    if "未达阈值" in lowered and "铁丝网" in lowered:
        return True
    if re.search(r"铁丝网分数\s*0\.\d+", lowered) and "未达" in lowered:
        return True
    return False


def _should_sync_barbwire_tag(
    stage1_json: dict[str, Any],
    text: str,
    *,
    kline_frame: Any = None,
) -> bool:
    if _barbwire_blocked(stage1_json):
        return False
    if _barbwire_keyword_negated(text):
        return False
    return _barbwire_candidate(stage1_json, kline_frame)


def _collect_detected_pattern_tags(
    stage1_json: dict[str, Any],
    *,
    kline_frame: Any = None,
) -> list[str]:
    """Merge model tags + entry_setup_type + key_signals heuristics (stable order)."""
    raw = stage1_json.get("detected_patterns") or []
    patterns: list[str] = []
    seen: set[str] = set()
    for p in raw:
        key = str(p).strip().lower()
        if key and key not in seen:
            seen.add(key)
            patterns.append(key)

    est = _entry_setup_type(stage1_json)
    for key in ENTRY_SETUP_TYPE_PATTERN_OVERLAY.get(est, ()):
        if key == "barbwire" and not _barbwire_candidate(stage1_json, kline_frame):
            continue
        if key not in seen:
            seen.add(key)
            patterns.append(key)

    cp = str(stage1_json.get("cycle_position", "") or "").strip().lower()
    for key in _CYCLE_POSITION_PATTERN_TAGS.get(cp, ()):
        if key == "barbwire" and not _barbwire_candidate(stage1_json, kline_frame):
            continue
        if key not in seen:
            seen.add(key)
            patterns.append(key)

    blob = " ".join(str(s) for s in (stage1_json.get("key_signals") or [])).lower()
    risk = str(stage1_json.get("risk_warning") or "").lower()
    text = f"{blob} {risk}"
    for keywords, tag in _PATTERN_KEYWORD_TAGS:
        if tag == "h1":
            if _mentions_hl_count_setup(text):
                for hl in ("h1", "h2", "l1", "l2"):
                    if re.search(rf"(?<![a-z]){hl}(?![a-z])", text) and hl not in seen:
                        seen.add(hl)
                        patterns.append(hl)
            continue
        if tag == "barbwire":
            if not any(k.lower() in text for k in keywords):
                continue
            if not _should_sync_barbwire_tag(stage1_json, text, kline_frame=kline_frame):
                continue
        if any(k.lower() in text for k in keywords) and tag not in seen:
            seen.add(tag)
            patterns.append(tag)

    return patterns


def sync_detected_patterns_field(
    stage1_json: dict[str, Any],
    *,
    kline_frame: Any = None,
) -> list[str]:
    """Write merged tags back into stage1_json['detected_patterns'] (in-place)."""
    patterns = _collect_detected_pattern_tags(stage1_json, kline_frame=kline_frame)
    stage1_json["detected_patterns"] = patterns
    return patterns


def merge_detected_patterns(
    stage1_json: dict[str, Any],
    *,
    kline_frame: Any = None,
) -> list[str]:
    """Union of detected_patterns and entry_setup_type overlay (stable order)."""
    return _collect_detected_pattern_tags(stage1_json, kline_frame=kline_frame)


def _mentions_hl_count_setup(text: str) -> bool:
    """True when narrative explicitly refers to H1/H2/L1/L2 count entry (not EMA gap count)."""
    return bool(_HL_COUNT_SETUP_RE.search(text))


def _entry_setup_type(stage1_json: dict[str, Any]) -> str:
    bar = stage1_json.get("bar_analysis")
    if not isinstance(bar, dict):
        return ""
    return str(bar.get("entry_setup_type") or "").strip().lower()


_HL_WORD_RE = re.compile(r"(?i)(?:high|low)\s*([12])(?![0-9])")


def _hl_tags_from_text(text: str) -> list[str]:
    tags: list[str] = []
    for m in _HL_WORD_RE.finditer(text):
        prefix = "h" if m.group(0).lower().startswith("h") else "l"
        tags.append(f"{prefix}{m.group(1)}")
    for hl in ("h1", "h2", "l1", "l2"):
        if re.search(rf"(?<![a-z]){hl}(?![a-z])", text) and hl not in tags:
            tags.append(hl)
    return tags


def ensure_detected_patterns_coherent(
    stage1_json: dict[str, Any],
    *,
    kline_frame: Any = None,
) -> bool:
    """Auto-add detected_patterns tags implied by key_signals / entry_setup_type."""
    before = list(stage1_json.get("detected_patterns") or [])
    sync_detected_patterns_field(stage1_json, kline_frame=kline_frame)
    patterns: list[str] = list(stage1_json.get("detected_patterns") or [])
    seen = {str(p).strip().lower() for p in patterns}

    blob = " ".join(str(s) for s in (stage1_json.get("key_signals") or [])).lower()
    risk = str(stage1_json.get("risk_warning") or "").lower()
    text = f"{blob} {risk}"

    changed = patterns != before

    if _mentions_hl_count_setup(text) or _hl_tags_from_text(text):
        for hl in _hl_tags_from_text(text) or _HL_PATTERN_KEYWORDS:
            if hl not in seen:
                seen.add(hl)
                patterns.append(hl)
                changed = True

    for keywords, required in _PATTERN_KEYWORD_TAGS:
        if required == "h1":
            continue
        if required == "barbwire":
            if not any(k.lower() in text for k in keywords):
                continue
            if not _should_sync_barbwire_tag(stage1_json, text, kline_frame=kline_frame):
                continue
        if any(k.lower() in text for k in keywords) and required not in seen:
            seen.add(required)
            patterns.append(required)
            changed = True

    est = _entry_setup_type(stage1_json)
    if est == "wedge" and "wedge" not in seen:
        patterns.append("wedge")
        changed = True
    if est == "breakout_pullback" and "breakout_pullback" not in seen:
        patterns.append("breakout_pullback")
        changed = True
    if est == "tr_boundary":
        for required in ("middle_range", "barbwire"):
            if required == "barbwire" and not _barbwire_candidate(stage1_json, kline_frame):
                continue
            if required not in seen:
                seen.add(required)
                patterns.append(required)
                changed = True

    if changed:
        stage1_json["detected_patterns"] = patterns
    return changed


def validate_detected_patterns_vs_key_signals(
    stage1: dict[str, Any],
    *,
    kline_frame: Any = None,
) -> list[str]:
    """Warn when narrative mentions a pattern but detected_patterns omits the router key."""
    errors: list[str] = []
    patterns = {str(p).strip().lower() for p in (stage1.get("detected_patterns") or [])}
    blob = " ".join(str(s) for s in (stage1.get("key_signals") or [])).lower()
    risk = str(stage1.get("risk_warning") or "").lower()
    text = f"{blob} {risk}"

    for keywords, required in _PATTERN_KEYWORD_TAGS:
        if required == "h1":
            if _mentions_hl_count_setup(text):
                if not patterns.intersection(_HL_PATTERN_KEYWORDS.keys()):
                    errors.append(
                        "key_signals mentions H1/H2/L1/L2 count setup but "
                        "detected_patterns lacks h1/h2/l1/l2"
                    )
            continue
        if required == "barbwire":
            if not any(k.lower() in text for k in keywords):
                continue
            if not _should_sync_barbwire_tag(stage1, text, kline_frame=kline_frame):
                continue
        if any(k.lower() in text for k in keywords):
            if required not in patterns:
                errors.append(
                    f"key_signals/risk_warning mentions pattern related to {required!r} "
                    f"but detected_patterns lacks {required!r}"
                )

    est = _entry_setup_type(stage1)
    if est == "wedge" and "wedge" not in patterns:
        errors.append(
            "bar_analysis.entry_setup_type=wedge requires detected_patterns to include 'wedge'"
        )
    if est == "breakout_pullback" and "breakout_pullback" not in patterns:
        errors.append(
            "bar_analysis.entry_setup_type=breakout_pullback requires "
            "detected_patterns to include 'breakout_pullback'"
        )
    if est == "tr_boundary":
        for required in ("middle_range", "barbwire"):
            if required == "barbwire" and not _barbwire_candidate(stage1, kline_frame):
                continue
            if required not in patterns:
                errors.append(
                    f"bar_analysis.entry_setup_type=tr_boundary requires "
                    f"detected_patterns to include {required!r}"
                )

    return errors
