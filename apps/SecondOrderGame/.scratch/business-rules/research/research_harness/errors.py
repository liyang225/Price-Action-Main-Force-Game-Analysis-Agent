from __future__ import annotations


class ResearchHarnessError(Exception):
    """Base error for the offline research harness."""


class ConfigError(ResearchHarnessError):
    """Raised when a research YAML file is invalid."""


class RuleEvaluationError(ResearchHarnessError):
    """Raised when a configured rule cannot be evaluated on provider data."""


class HistoryProviderError(ResearchHarnessError):
    """Raised when a history provider cannot satisfy a request."""
