"""Validation retry policy: which errors may retry and immutable field guards."""
from __future__ import annotations

import re
from typing import Any, Literal

StageName = Literal["stage1", "stage2"]

# Format-like invalid field prefixes — safe to retry without changing trade thesis.
_FORMAT_PREFIXES: tuple[str, ...] = (
    "gate_trace",
    "decision_trace",
    "bar_by_bar_summary",
    "node_overrides",
    "bar_range",
    "incremental",
    "next_bar_prediction",
    "next_cycle_prediction",
    "decision.reasoning",
)

# Semantic errors that must NOT trigger retry (program should downgrade instead).
_NO_RETRY_PREFIXES: tuple[str, ...] = (
    "metrics:",
    "trace:§14",
    "s2:order_direction",
)

IMMUTABLE_FIELDS: dict[StageName, tuple[str, ...]] = {
    # gate_result excluded: program _repair_gate_result may change wait/unknown↔proceed
    # during normalize; raw weakening is checked separately in detect_cheat.
    "stage1": ("direction", "cycle_position"),
    "stage2": (),  # stage2 direction lives in diagnosis_summary; checked separately
}

IMMUTABLE_DIAG_SUMMARY: tuple[str, ...] = ("cycle_position",)


def max_retries_for_category(category: str, settings: Any) -> int:
    """Return allowed retry count for a validation category."""
    if category == "e":
        return 0
    if not getattr(settings, "retry_enabled", True):
        return 0
    base = int(getattr(settings, "retry_max", 3) or 0)
    if category in ("a", "b", "d"):
        return base
    if category == "c":
        return min(base, int(getattr(settings, "retry_max_semantic", 1) or 1))
    return 0


def should_retry(
    category: str,
    invalid_fields: list[str],
    missing_fields: list[str],
    *,
    attempt: int,
    settings: Any,
) -> bool:
    """Whether another API call is warranted."""
    if category == "e":
        return False
    if attempt >= max_retries_for_category(category, settings):
        return False
    if category in ("a", "b", "d"):
        return True
    if category != "c":
        return False
    fields = list(invalid_fields or []) + list(missing_fields or [])
    if not fields:
        return False
    if any(_starts_any(f, _NO_RETRY_PREFIXES) for f in fields):
        return False
    if any(_starts_any(f, _FORMAT_PREFIXES) for f in fields):
        return True
    if any(
        f.startswith(("s1:", "s2:", "gate:", "trace:", "breakout_price:", "signal_chain:"))
        for f in fields
    ):
        # Default: one semantic retry if enabled
        return attempt < int(getattr(settings, "retry_max_semantic", 1) or 1)
    return False


def _starts_any(field: str, prefixes: tuple[str, ...]) -> bool:
    text = str(field or "")
    return any(text.startswith(p) or p in text for p in prefixes)


def _get_path(obj: dict[str, Any], path: str) -> Any:
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _changed_fields_justify(after_raw: dict[str, Any] | None, field: str) -> bool:
    if not isinstance(after_raw, dict):
        return False
    delta = after_raw.get("incremental_delta")
    if not isinstance(delta, dict):
        return False
    changed = delta.get("changed_fields")
    return isinstance(changed, list) and field in changed


def _direction_change_justified(after_raw: dict[str, Any] | None) -> bool:
    """Incremental §2.3 override or explicit incremental_delta may change direction."""
    if not isinstance(after_raw, dict):
        return False
    if _changed_fields_justify(after_raw, "direction"):
        return True
    direction = str(after_raw.get("direction") or "").strip().lower()
    if direction not in ("bullish", "bearish", "neutral"):
        return False
    overrides = after_raw.get("node_overrides")
    if not isinstance(overrides, list):
        return False
    for ov in overrides:
        if not isinstance(ov, dict):
            continue
        node_id = str(ov.get("node_id", "")).replace("§", "").strip()
        if node_id != "2.3":
            continue
        branch = str(ov.get("branch") or "").strip().lower()
        if branch and branch == direction:
            return True
        if (
            direction in ("bullish", "bearish")
            and str(ov.get("answer", "")).strip() == "是"
            and branch in ("", direction)
        ):
            return True
    return False


def detect_cheat(
    stage: StageName,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    *,
    before_raw: dict[str, Any] | None = None,
    after_raw: dict[str, Any] | None = None,
    feedback_mentioned: set[str] | None = None,
) -> list[str]:
    """Return human-readable cheat flags when immutable fields changed without cause."""
    if not before or not isinstance(after, dict):
        return []
    mentioned = feedback_mentioned or set()
    violations: list[str] = []
    raw_after = after_raw if isinstance(after_raw, dict) else after

    for key in IMMUTABLE_FIELDS.get(stage, ()):
        if key in mentioned:
            continue
        if stage == "stage1" and key == "direction" and _direction_change_justified(raw_after):
            continue
        if stage == "stage1" and key == "cycle_position" and _changed_fields_justify(
            raw_after, "cycle_position"
        ):
            continue
        b = before.get(key)
        a = after.get(key)
        if b is not None and a is not None and str(b) != str(a):
            violations.append(f"{key}: {b!r} → {a!r}")

    if stage == "stage1" and "gate_result" not in mentioned:
        raw_before = before_raw if isinstance(before_raw, dict) else before
        raw_after = after_raw if isinstance(after_raw, dict) else after
        br = str(raw_before.get("gate_result") or "").strip().lower()
        ar = str(raw_after.get("gate_result") or "").strip().lower()
        norm_b = str(before.get("gate_result") or "").strip().lower()
        norm_a = str(after.get("gate_result") or "").strip().lower()
        # Normalizer may repair wait/unknown→proceed; skip if effective values agree.
        if not (norm_b and norm_a and norm_b == norm_a):
            if br == "proceed" and ar in ("wait", "unknown"):
                violations.append(f"gate_result: {br!r} → {ar!r}")

    if stage == "stage2":
        bsum = before.get("diagnosis_summary") if isinstance(before.get("diagnosis_summary"), dict) else {}
        asum = after.get("diagnosis_summary") if isinstance(after.get("diagnosis_summary"), dict) else {}
        for key in IMMUTABLE_DIAG_SUMMARY:
            path = f"diagnosis_summary.{key}"
            if path in mentioned or key in mentioned:
                continue
            b = bsum.get(key)
            a = asum.get(key)
            if b is not None and a is not None and str(b) != str(a):
                violations.append(f"{path}: {b!r} → {a!r}")

    return violations


def extract_feedback_targets(invalid_fields: list[str], missing_fields: list[str]) -> set[str]:
    """Map error lines to field paths mentioned in retry feedback."""
    targets: set[str] = set()
    for raw in list(missing_fields or []) + list(invalid_fields or []):
        text = str(raw)
        for key in (
            "direction",
            "cycle_position",
            "gate_result",
            "diagnosis_summary",
            "order_type",
            "next_bar_prediction",
            "next_cycle_prediction",
        ):
            if key in text:
                targets.add(key)
    return targets
