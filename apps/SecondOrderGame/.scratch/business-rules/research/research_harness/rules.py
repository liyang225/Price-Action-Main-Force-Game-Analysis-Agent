from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .errors import ConfigError, RuleEvaluationError


SUPPORTED_OPERATORS = frozenset(
    {
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "between",
        "in",
        "not_in",
        "is_null",
        "not_null",
    }
)


class RuleExpression(Protocol):
    def evaluate(self, record: Mapping[str, Any]) -> bool: ...


@dataclass(frozen=True)
class Rule:
    label: str
    when: RuleExpression

    def matches(self, record: Mapping[str, Any]) -> bool:
        return self.when.evaluate(record)


@dataclass(frozen=True)
class _Predicate:
    field: str
    operator: str
    value: Any = None

    def evaluate(self, record: Mapping[str, Any]) -> bool:
        actual = _read_field(record, self.field)
        if self.operator == "is_null":
            return actual is None
        if self.operator == "not_null":
            return actual is not None
        if self.operator == "eq":
            return actual == self.value
        if self.operator == "ne":
            return actual != self.value
        if actual is None:
            return False

        try:
            if self.operator == "gt":
                return actual > self.value
            if self.operator == "gte":
                return actual >= self.value
            if self.operator == "lt":
                return actual < self.value
            if self.operator == "lte":
                return actual <= self.value
            if self.operator == "between":
                lower, upper = self.value
                return lower <= actual <= upper
            if self.operator == "in":
                return actual in self.value
            if self.operator == "not_in":
                return actual not in self.value
        except TypeError as exc:
            raise RuleEvaluationError(
                f"field {self.field!r} cannot use operator {self.operator!r} "
                f"with value {self.value!r}: {actual!r}"
            ) from exc

        raise RuleEvaluationError(f"unsupported operator at runtime: {self.operator!r}")


@dataclass(frozen=True)
class _All:
    expressions: tuple[RuleExpression, ...]

    def evaluate(self, record: Mapping[str, Any]) -> bool:
        return all(expression.evaluate(record) for expression in self.expressions)


@dataclass(frozen=True)
class _Any:
    expressions: tuple[RuleExpression, ...]

    def evaluate(self, record: Mapping[str, Any]) -> bool:
        return any(expression.evaluate(record) for expression in self.expressions)


@dataclass(frozen=True)
class _Not:
    expression: RuleExpression

    def evaluate(self, record: Mapping[str, Any]) -> bool:
        return not self.expression.evaluate(record)


def load_rule_expression(raw: Any, path: str = "when") -> RuleExpression:
    """Parse the small, declarative rule language without executing code."""
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path} must be a structured mapping")

    keys = set(raw)
    compound_keys = keys & {"all", "any", "not"}
    if compound_keys:
        if len(compound_keys) != 1 or len(keys) != 1:
            unknown = sorted(keys - compound_keys)
            suffix = f"; unknown key(s): {', '.join(unknown)}" if unknown else ""
            raise ConfigError(f"{path} must contain exactly one compound operator{suffix}")
        operator = next(iter(compound_keys))
        value = raw[operator]
        if operator == "not":
            return _Not(load_rule_expression(value, f"{path}.not"))
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
            raise ConfigError(f"{path}.{operator} must be a non-empty list")
        expressions = tuple(
            load_rule_expression(item, f"{path}.{operator}[{index}]")
            for index, item in enumerate(value)
        )
        return _All(expressions) if operator == "all" else _Any(expressions)

    allowed_keys = {"field", "op", "value"}
    unknown_keys = keys - allowed_keys
    if unknown_keys:
        raise ConfigError(
            f"{path} contains unknown key(s): {', '.join(sorted(map(str, unknown_keys)))}"
        )
    missing = {"field", "op"} - keys
    if missing:
        raise ConfigError(f"{path} is missing key(s): {', '.join(sorted(missing))}")

    field = raw["field"]
    operator = raw["op"]
    if not isinstance(field, str) or not field.strip():
        raise ConfigError(f"{path}.field must be a non-empty string")
    if not isinstance(operator, str) or operator not in SUPPORTED_OPERATORS:
        raise ConfigError(f"{path}.op has unsupported operator: {operator!r}")

    null_operator = operator in {"is_null", "not_null"}
    if null_operator and "value" in keys:
        raise ConfigError(f"{path}.value is not allowed for operator {operator!r}")
    if not null_operator and "value" not in keys:
        raise ConfigError(f"{path}.value is required for operator {operator!r}")

    value = raw.get("value")
    if operator == "between":
        if (
            not isinstance(value, Sequence)
            or isinstance(value, (str, bytes))
            or len(value) != 2
        ):
            raise ConfigError(f"{path}.value must contain two bounds for 'between'")
        value = tuple(value)
    elif operator in {"in", "not_in"}:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise ConfigError(f"{path}.value must be a list for {operator!r}")
        value = tuple(value)
    elif isinstance(value, Mapping):
        raise ConfigError(f"{path}.value must be a scalar or list")

    return _Predicate(field=field.strip(), operator=operator, value=value)


def _read_field(record: Mapping[str, Any], field: str) -> Any:
    value: Any = record
    traversed: list[str] = []
    for part in field.split("."):
        traversed.append(part)
        if not isinstance(value, Mapping) or part not in value:
            raise RuleEvaluationError(f"provider row is missing field {'.'.join(traversed)!r}")
        value = value[part]
    return value
