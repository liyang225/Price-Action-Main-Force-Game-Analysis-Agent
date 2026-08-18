"""Canonical model-facing projection of decision materials.

The model never reads raw material structures; it only sees a per-stage
white-list of fields.  Each reasoning stage (cycle / participant / behavior)
gets its own projection so the three prompts never share an accidental field
set (see docs/llm-injection-schema.md).
"""

from __future__ import annotations

from typing import Any, Mapping


_MARKET_METADATA_KEYS = (
    "status",
    "source",
    "created_at",
    "data_date",
    "decision_date",
    "age_days",
    "usable_for_analysis",
    "reason",
)

# Per-stage white-lists (strict field enumeration).  Fields present in the
# bridge materials but absent from every list below are audit/program-only and
# must never reach the model: material_cache, material_snapshot,
# probability_chain, position_cases, market_window.
_CYCLE_MATERIAL_KEYS = frozenset(
    {
        "market_analysis",
        "sentiment_breadth",
        "limit_pool",
        "pa_stage1_analysis",
        "sector_analysis",
        "user_context",
        "news",
        "scored_news",
        "subject_purpose",
    }
)

_PARTICIPANT_MATERIAL_KEYS = _CYCLE_MATERIAL_KEYS | {
    "pa_stage2",
    "dragon_tiger",
    "capital_flow",
}

_FORECAST_MATERIAL_KEYS = _PARTICIPANT_MATERIAL_KEYS | {"participant_priors"}


def project_model_materials(
    materials: Mapping[str, Any],
    *,
    allowed_keys: frozenset[str] | None = None,
) -> dict[str, Any]:
    """White-list ``materials`` down to ``allowed_keys`` (None = keep all)."""
    if not isinstance(materials, Mapping):
        raise TypeError("materials must be a mapping")
    result: dict[str, Any] = {}
    for key, value in materials.items():
        if allowed_keys is not None and key not in allowed_keys:
            continue
        if key == "market_analysis" and isinstance(value, Mapping):
            result[key] = _project_market_analysis(value)
        else:
            result[key] = value
    return result


def project_model_payload(
    payload: Mapping[str, Any],
    *,
    allowed_keys: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Project a payload that may wrap materials under a ``materials`` key."""
    if not isinstance(payload, Mapping):
        raise TypeError("model payload must be a mapping")
    nested = payload.get("materials")
    if isinstance(nested, Mapping):
        return {
            **dict(payload),
            "materials": project_model_materials(nested, allowed_keys=allowed_keys),
        }
    return project_model_materials(payload, allowed_keys=allowed_keys)


def project_cycle_payload(materials: Mapping[str, Any]) -> dict[str, Any]:
    """情绪周期判断投影：白名单。情绪指数保留作为辅助参考，但不用于阈值映射。"""
    if not isinstance(materials, Mapping) or not materials:
        raise ValueError("情绪周期判断缺少分析材料")
    return project_model_payload(materials, allowed_keys=_CYCLE_MATERIAL_KEYS)


def project_reasoning_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """参与者识别投影：白名单 + 连续情绪信号（非周期环节可读）。"""
    return _project_with_sentiment_signal(payload, _PARTICIPANT_MATERIAL_KEYS)


def project_forecast_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """行为推演投影：参与者识别白名单 + participant_priors + 连续情绪信号。"""
    return _project_with_sentiment_signal(payload, _FORECAST_MATERIAL_KEYS)


def _project_with_sentiment_signal(
    payload: Mapping[str, Any], allowed_keys: frozenset[str]
) -> dict[str, Any]:
    projected = project_model_payload(payload, allowed_keys=allowed_keys)
    signal = _sentiment_signal(_materials_of(payload))
    if signal is not None:
        projected["sentiment_signal"] = signal
    return projected


def _materials_of(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = payload.get("materials")
    return nested if isinstance(nested, Mapping) else payload


def _sentiment_signal(materials: Mapping[str, Any]) -> dict[str, Any] | None:
    """Expose the continuous non-cycle signal for non-cycle reasoning stages."""
    sector = materials.get("sector_analysis")
    if not isinstance(sector, Mapping):
        return None
    details = sector.get("sentiment_index_details")
    details = details if isinstance(details, Mapping) else {}
    status = str(details.get("status") or "unknown")
    return {
        "status": status,
        "usable": status not in {"market_data_unavailable", "unknown"},
        "index": sector.get("sentiment_index"),
        "previous_index": details.get("previous_index"),
        "daily_delta": details.get("daily_delta"),
        "news_delta": details.get("news_delta"),
        "price_action_delta": details.get("price_action_delta"),
        "reasoning_role": "strength_change_disagreement_and_risk",
        "cycle_classification_role": "excluded",
    }


def _project_market_analysis(value: Mapping[str, Any]) -> dict[str, Any]:
    projected = {
        key: value[key] for key in _MARKET_METADATA_KEYS if key in value
    }
    sections = value.get("display_sections")
    if isinstance(sections, list | tuple):
        unique: list[Any] = []
        seen: set[tuple[str, str]] = set()
        for section in sections:
            if isinstance(section, Mapping):
                identity = (
                    str(section.get("title") or ""),
                    str(section.get("content") or ""),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                unique.append(dict(section))
            else:
                unique.append(section)
        projected["display_sections"] = unique
    return projected


__all__ = [
    "project_model_materials",
    "project_model_payload",
    "project_cycle_payload",
    "project_reasoning_payload",
    "project_forecast_payload",
]
