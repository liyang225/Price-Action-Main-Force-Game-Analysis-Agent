"""Reproducible, offline business-rules study helpers.

The package deliberately keeps the research pipeline separate from the live
``SecondOrderGame.src`` modules.  Feature engineering, rule evaluation and
statistics are deterministic functions; only :mod:`collector` talks to an
injected Futu-like client.
"""

from .features import engineer_features
from .rules import FROZEN_LABELS, evaluate_rule_masks, load_rule_config, resolve_fixed_priority
from .stats import overlap_statistics

__all__ = [
    "FROZEN_LABELS",
    "engineer_features",
    "evaluate_rule_masks",
    "load_rule_config",
    "overlap_statistics",
    "resolve_fixed_priority",
]
