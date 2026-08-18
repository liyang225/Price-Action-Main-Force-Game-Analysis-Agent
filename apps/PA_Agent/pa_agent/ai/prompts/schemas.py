"""JSON schemas for Stage 1 and Stage 2 AI outputs."""
from __future__ import annotations

# ── Override item schema ──────────────────────────────────────────────────────

_NODE_OVERRIDE_ITEM: dict = {
    "type": "object",
    "required": ["node_id", "answer", "override_reason"],
    "properties": {
        "node_id": {"type": "string"},
        "answer": {
            "type": "string",
            "enum": ["是", "否", "中性", "等待", "不适用"],
        },
        "branch": {"type": ["string", "null"]},
        "override_reason": {"type": "string", "minLength": 1},
    },
    "additionalProperties": True,
}

# ── Shared trace item schemas (二元决策树) ─────────────────────────────────────

_TRACE_ITEM: dict = {
    "type": "object",
    "required": ["node_id", "question", "answer", "reason", "bar_range"],
    "properties": {
        "node_id": {"type": "string"},
        "question": {"type": "string"},
        "answer": {
            "type": "string",
            "enum": ["是", "否", "中性", "等待", "不适用"],
        },
        "action": {"type": "string"},
        "reason": {"type": "string"},
        "branch": {"type": ["string", "null"]},
        "next_node": {"type": ["string", "null"]},
        "skipped": {"type": "boolean"},
        "section": {"type": "string"},
        "bar_range": {
            "type": "string",
            "description": "K-line basis e.g. K50-K1 (seq1=newest closed bar)",
        },
        "bar_from": {
            "type": "integer",
            "minimum": 1,
            "description": "Older bar seq (larger number)",
        },
        "bar_to": {
            "type": "integer",
            "minimum": 1,
            "description": "Newer bar seq (smaller, often 1)",
        },
        # Override trace fields (optional, present when overridden_by_ai=True)
        "program_answer": {"type": "string"},
        "program_branch": {"type": ["string", "null"]},
        "override_reason": {"type": "string"},
        "overridden_by_ai": {"type": "boolean"},
    },
    "additionalProperties": True,
}

_TERMINAL: dict = {
    "type": "object",
    "required": ["node_id", "outcome", "label"],
    "properties": {
        "node_id": {"type": "string"},
        "outcome": {
            "type": "string",
            "enum": ["wait", "reject", "trade", "proceed"],
        },
        "label": {"type": "string"},
    },
    "additionalProperties": True,
}

_SIGNAL_BAR: dict = {
    "type": "object",
    "required": ["bar", "quality", "reason"],
    "properties": {
        "bar": {"type": ["string", "null"]},
        "quality": {"type": "string", "enum": ["strong", "medium", "weak", "invalid"]},
        "pattern": {"type": "string"},
        "reason": {"type": "string"},
    },
    "additionalProperties": True,
}

_ENTRY_BAR: dict = {
    "type": "object",
    "required": ["strength", "follow_through"],
    "properties": {
        "bar": {"type": ["string", "null"]},
        "strength": {"type": "string", "enum": ["strong", "weak", "not_triggered"]},
        "follow_through": {"type": ["boolean", "string", "null"]},
        "still_valid": {"type": ["boolean", "null"]},
        "freshness": {"type": "string", "enum": ["fresh", "pending", "stale", "invalid"]},
    },
    "additionalProperties": True,
}

_SECOND_ENTRY: dict = {
    "type": "object",
    "properties": {
        "is_second_entry": {"type": "boolean"},
        "type": {"type": "string"},
    },
    "additionalProperties": True,
}

_BAR_ANALYSIS: dict = {
    "type": "object",
    "properties": {
        "always_in": {"type": "string", "enum": ["long", "short", "neutral"]},
        "last_closed_bar": {"type": "string"},
        "bar_type": {
            "type": "string",
            "enum": [
                "trend_bull", "trend_bear", "doji", "inside",
                "outside_bull", "outside_bear", "flat", "other",
            ],
        },
        "signal_bar": _SIGNAL_BAR,
        "entry_setup_type": {"type": "string"},
        "follow_through": {"type": ["string", "boolean", "null"]},
        "entry_bar": _ENTRY_BAR,
        "second_entry": _SECOND_ENTRY,
        "tr_position": {"type": "string"},
        "breakout_quality": {"type": "string"},
    },
    "additionalProperties": True,
}

_BAR_BY_BAR_ITEM: dict = {
    "type": "object",
    "required": [
        "bar",
        "role",
        "bar_type",
        "context_effect",
        "follow_through",
        "trapped_side",
        "reason",
    ],
    "properties": {
        "bar": {"type": "string"},
        "role": {
            "type": "string",
            "enum": [
                "structure", "signal", "entry", "confirmation",
                "noise", "trap", "climax", "test",
            ],
        },
        "bar_type": {
            "type": "string",
            "enum": [
                "trend_bull", "trend_bear", "doji", "inside",
                "outside_bull", "outside_bear", "flat", "other",
            ],
        },
        "context_effect": {
            "type": "string",
            "enum": [
                "strengthens_bull", "weakens_bull", "strengthens_bear",
                "weakens_bear", "neutral", "transition",
                "weakened_bull", "weakened_bear",
            ],
        },
        "follow_through": {"type": "string", "enum": ["yes", "no", "pending", "failed"]},
        "trapped_side": {"type": "string", "enum": ["bulls", "bears", "both", "none", "unknown"]},
        "reason": {"type": "string"},
    },
    "additionalProperties": True,
}

# ── Stage 1 schema ────────────────────────────────────────────────────────────

STAGE1_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": [
        "cycle_position",
        "direction",
        "diagnosis_confidence",
        "market_phase",
        "detected_patterns",
        "key_signals",
        "htf_context",
        "entry_setup",
        "strategy_files_needed",
        "bar_by_bar_summary",
        "gate_trace",
        "gate_result",
    ],
    "properties": {
        "cycle_position": {
            "type": "string",
            "enum": [
                "spike", "micro_channel", "tight_channel", "normal_channel",
                "broad_channel", "trending_tr", "trading_range", "extreme_tr", "unknown",
            ],
        },
        "alternative_cycle_position": {"type": ["string", "null"]},
        "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
        "diagnosis_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "spike_stage": {
            "type": ["string", "null"],
            "enum": ["active", "ending", "transitioning", None],
        },
        "climax_risk": {
            "type": ["string", "null"],
            "enum": ["none", "warning", "triggered", None],
        },
        "market_phase": {"type": "string", "enum": ["stable", "transitioning"]},
        "support_levels": {"type": "array", "items": {"type": "string"}},
        "resistance_levels": {"type": "array", "items": {"type": "string"}},
        "transition_risk": {
            "type": ["string", "null"],
            "enum": ["high", "medium", "low", None],
        },
        "detected_patterns": {"type": "array", "items": {"type": "string"}},
        "key_signals": {"type": "array", "items": {"type": "string"}},
        "htf_context": {"type": "string"},
        "trend_context": {
            "type": "object",
            "properties": {
                "background_direction": {
                    "type": "string",
                    "enum": ["bullish", "bearish", "neutral"],
                },
                "trading_direction": {
                    "type": "string",
                    "enum": ["bullish", "bearish", "neutral"],
                },
                "primary_direction": {
                    "type": "string",
                    "enum": ["bullish", "bearish", "neutral"],
                },
                "conflict": {"type": "boolean"},
                "relationship": {
                    "type": "string",
                    "enum": ["aligned", "conflict", "neutral_background", "mixed"],
                },
                "recent_spike": {
                    "type": ["string", "null"],
                    "enum": ["bullish", "bearish", None],
                },
                "with_trend_rule": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "entry_setup": {"type": "string"},
        "strategy_files_needed": {"type": "array", "items": {"type": "string"}},
        "risk_warning": {"type": "string"},
        "bar_analysis": _BAR_ANALYSIS,
        "bar_by_bar_summary": {
            "type": "array",
            "minItems": 1,
            "items": _BAR_BY_BAR_ITEM,
        },
        "gate_trace": {
            "type": "array",
            "minItems": 1,
            "items": _TRACE_ITEM,
        },
        "gate_result": {
            "type": "string",
            "enum": ["proceed", "wait", "unknown"],
        },
        "incremental_delta": {
            "type": "object",
            "properties": {
                "new_closed_bars": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "changed_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "summary": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "node_overrides": {
            "type": "array",
            "items": _NODE_OVERRIDE_ITEM,
        },
    },
    "allOf": [
        # spike only requires spike_stage (micro_channel may keep spike_stage null)
        {
            "if": {
                "properties": {"cycle_position": {"const": "spike"}},
                "required": ["cycle_position"],
            },
            "then": {
                "properties": {
                    "spike_stage": {"type": "string", "enum": ["active", "ending", "transitioning"]}
                },
                "required": ["spike_stage"],
            },
        },
        # transitioning market_phase requires transition_risk to be non-null
        {
            "if": {
                "properties": {"market_phase": {"const": "transitioning"}},
                "required": ["market_phase"],
            },
            "then": {
                "properties": {
                    "transition_risk": {"type": "string", "enum": ["high", "medium", "low"]}
                },
                "required": ["transition_risk"],
            },
        },
    ],
    "additionalProperties": True,
}


# ── Stage 2 schema ────────────────────────────────────────────────────────────

_DECISION_BASE: dict = {
    "type": "object",
    "required": [
        "order_type",
        "reasoning",
        "diagnosis_confidence",
        "diagnosis_confidence_reasoning",
        "trade_confidence",
        "trade_confidence_reasoning",
        "estimated_win_rate",
        "estimated_win_rate_reasoning",
        "key_factors",
        "watch_points",
        "risk_assessment",
    ],
    "properties": {
        "order_direction": {"type": ["string", "null"]},
        "order_type": {
            "type": "string",
            "enum": ["限价单", "突破单", "市价单", "不下单"],
        },
        "entry_price": {"type": ["number", "null"]},
        "entry_basis_bar": {"type": ["string", "null"]},
        "entry_basis_extreme": {"type": ["string", "null"], "enum": ["high", "low", None]},
        "entry_rule": {"type": ["string", "null"]},
        "take_profit_price": {"type": ["number", "null"]},
        "take_profit_price_2": {"type": ["number", "null"]},
        "stop_loss_price": {"type": ["number", "null"]},
        "reasoning": {"type": "string", "minLength": 1, "maxLength": 280},
        "diagnosis_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "diagnosis_confidence_reasoning": {"type": "string"},
        "trade_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "trade_confidence_reasoning": {"type": "string"},
        "estimated_win_rate": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
        "estimated_win_rate_reasoning": {"type": ["string", "null"]},
        "key_factors": {"type": "array", "items": {"type": "string"}},
        "watch_points": {"type": "array", "items": {"type": "string"}},
        "risk_assessment": {"type": "string"},
        "invalidation_condition": {"type": ["string", "null"]},
    },
    "allOf": [
        # 不下单 → all price fields and direction must be null
        {
            "if": {
                "properties": {"order_type": {"const": "不下单"}},
                "required": ["order_type"],
            },
            "then": {
                "properties": {
                    "entry_price": {"type": "null"},
                    "entry_basis_bar": {"type": "null"},
                    "entry_basis_extreme": {"type": "null"},
                    "entry_rule": {"type": "null"},
                    "take_profit_price": {"type": "null"},
                    "take_profit_price_2": {"type": "null"},
                    "stop_loss_price": {"type": "null"},
                    "order_direction": {"type": "null"},
                    "estimated_win_rate": {"type": "null"},
                },
            },
        },
        # 有下单 → price fields must be numbers, direction must be 做多/做空
        {
            "if": {
                "properties": {
                    "order_type": {"enum": ["限价单", "突破单", "市价单"]}
                },
                "required": ["order_type"],
            },
            "then": {
                "properties": {
                    "entry_price": {"type": "number"},
                    "take_profit_price": {"type": "number"},
                    "take_profit_price_2": {"type": "number"},
                    "stop_loss_price": {"type": "number"},
                    "order_direction": {"type": "string", "enum": ["做多", "做空"]},
                    "estimated_win_rate": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": [
                    "entry_price",
                    "take_profit_price",
                    "take_profit_price_2",
                    "stop_loss_price",
                    "order_direction",
                    "estimated_win_rate",
                ],
            },
        },
        # 突破单必须说明挂单依据，避免把 entry_price 填在 K 线中部。
        {
            "if": {
                "properties": {"order_type": {"const": "突破单"}},
                "required": ["order_type"],
            },
            "then": {
                "properties": {
                    "entry_basis_bar": {"type": "string"},
                    "entry_basis_extreme": {"type": "string", "enum": ["high", "low"]},
                    "entry_rule": {"type": "string"},
                },
                "required": ["entry_basis_bar", "entry_basis_extreme", "entry_rule"],
            },
        },
    ],
    "additionalProperties": True,
}

# ── Next bar prediction sub-schemas (R2.1, R2.2) ──────────────────────────────

_NEXT_BAR_PROBABILITIES: dict = {
    "type": ["object", "null"],
    "required": ["bullish", "bearish", "neutral"],
    "properties": {
        "bullish": {"type": "integer", "minimum": 0, "maximum": 100},
        "bearish": {"type": "integer", "minimum": 0, "maximum": 100},
        "neutral": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "additionalProperties": False,
}

_NEXT_BAR_PREDICTION: dict = {
    "type": "object",
    "required": ["direction", "probabilities", "reasoning", "unpredictable", "features_used"],
    "properties": {
        "direction": {
            "type": ["string", "null"],
            "enum": ["bullish", "bearish", "neutral", None],
        },
        "probabilities": _NEXT_BAR_PROBABILITIES,
        "reasoning": {"type": "string", "minLength": 1, "maxLength": 1500},
        "unpredictable": {"type": "boolean"},
        "features_used": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "stage1_diagnosis",
                    "kline_features",
                    "analysis_history",
                    "experience_library",
                    "stage2_decision",
                    "previous_prediction_summary",
                ],
            },
            "uniqueItems": True,
        },
    },
    "allOf": [
        # unpredictable=false → direction must be non-null string, probabilities must be object
        {
            "if": {
                "properties": {"unpredictable": {"const": False}},
                "required": ["unpredictable"],
            },
            "then": {
                "properties": {
                    "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                    "probabilities": {"type": "object"},
                },
            },
        },
        # unpredictable=true → direction=null, probabilities=null
        {
            "if": {
                "properties": {"unpredictable": {"const": True}},
                "required": ["unpredictable"],
            },
            "then": {
                "properties": {
                    "direction": {"type": "null"},
                    "probabilities": {"type": "null"},
                },
            },
        },
    ],
    "additionalProperties": False,
}

# ── Next cycle prediction sub-schemas ────────────────────────────────────────

from pa_agent.ai.cycle_enums import CYCLE_ENUM as _CYCLE_ENUM  # noqa: E402

_NEXT_CYCLE_PROBABILITIES: dict = {
    "type": ["object", "null"],
    "required": list(_CYCLE_ENUM),
    "properties": {k: {"type": "integer", "minimum": 0, "maximum": 100} for k in _CYCLE_ENUM},
    "additionalProperties": False,
}

_NEXT_CYCLE_PREDICTION: dict = {
    "type": "object",
    "required": ["cycle", "direction", "probabilities", "reasoning", "unpredictable", "features_used"],
    "properties": {
        "cycle": {
            "type": ["string", "null"],
            "enum": list(_CYCLE_ENUM) + [None],
        },
        "direction": {
            "type": ["string", "null"],
            "enum": ["bullish", "bearish", "neutral", None],
        },
        "probabilities": _NEXT_CYCLE_PROBABILITIES,
        "reasoning": {"type": "string", "minLength": 1, "maxLength": 1500},
        "unpredictable": {"type": "boolean"},
        "features_used": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "stage1_diagnosis",
                    "kline_features",
                    "analysis_history",
                    "experience_library",
                    "stage2_decision",
                    "previous_prediction_summary",
                ],
            },
            "uniqueItems": True,
        },
    },
    "allOf": [
        # unpredictable=false → cycle must be non-null enum string, probabilities must be object
        {
            "if": {
                "properties": {"unpredictable": {"const": False}},
                "required": ["unpredictable"],
            },
            "then": {
                "properties": {
                    "cycle": {"type": "string", "enum": list(_CYCLE_ENUM)},
                    "probabilities": {"type": "object"},
                },
            },
        },
        # unpredictable=true → cycle=null, direction=null, probabilities=null
        {
            "if": {
                "properties": {"unpredictable": {"const": True}},
                "required": ["unpredictable"],
            },
            "then": {
                "properties": {
                    "cycle": {"type": "null"},
                    "direction": {"type": "null"},
                    "probabilities": {"type": "null"},
                },
            },
        },
    ],
    "additionalProperties": False,
}

STAGE2_SCHEMA: dict = {
    "type": "object",
    "required": [
        "decision",
        "diagnosis_summary",
        "decision_trace",
        "terminal",
        "next_bar_prediction",
        "next_cycle_prediction",
    ],
    "properties": {
        "decision": _DECISION_BASE,
        "diagnosis_summary": {
            "type": "object",
            "required": ["cycle_position", "direction", "key_signals"],
            "properties": {
                "cycle_position": {"type": "string"},
                "direction": {"type": "string"},
                "key_signals": {"type": "array", "items": {"type": "string"}},
            },
        },
        "decision_trace": {
            "type": "array",
            "items": _TRACE_ITEM,
        },
        "terminal": _TERMINAL,
        "bar_analysis": _BAR_ANALYSIS,
        "gate_shortcircuited": {"type": "boolean"},
        "next_bar_prediction": _NEXT_BAR_PREDICTION,
        "next_cycle_prediction": _NEXT_CYCLE_PREDICTION,
        "node_overrides": {
            "type": "array",
            "items": _NODE_OVERRIDE_ITEM,
        },
    },
    "additionalProperties": True,
}
