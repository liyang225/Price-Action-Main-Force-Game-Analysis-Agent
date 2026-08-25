from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

from .errors import ConfigError
from .models import DataConfig, Instrument, OutputConfig, ResearchConfig
from .rules import Rule, load_rule_expression


def load_research_config(path: str | Path) -> ResearchConfig:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read config {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc

    root = _require_mapping(raw, "config")
    _check_keys(root, {"version", "data", "rules", "output"}, {"version", "data", "rules"}, "config")

    version = root["version"]
    if type(version) is not int or version != 1:
        raise ConfigError(f"config.version must be 1, got {version!r}")

    data = _load_data(root["data"])
    rules = _load_rules(root["rules"])
    output = _load_output(root.get("output"), config_path.parent)
    return ResearchConfig(version=version, data=data, rules=rules, output=output)


def _load_data(raw: Any) -> DataConfig:
    data = _require_mapping(raw, "data")
    allowed = {
        "provider",
        "provider_options",
        "start",
        "end",
        "period",
        "instruments",
    }
    required = {"provider", "start", "end", "period", "instruments"}
    _check_keys(data, allowed, required, "data")

    provider_raw = data["provider"]
    if not isinstance(provider_raw, str) or not provider_raw.strip():
        raise ConfigError("data.provider must be a non-empty string")
    provider = provider_raw.strip().lower()

    options = data.get("provider_options", {})
    options = _require_mapping(options, "data.provider_options")
    if not all(isinstance(key, str) for key in options):
        raise ConfigError("data.provider_options keys must be strings")
    if provider == "futu":
        _validate_futu_options(options)

    start = _parse_date(data["start"], "data.start")
    end = _parse_date(data["end"], "data.end")
    if start > end:
        raise ConfigError("data.start must not be after data.end")

    period = _parse_period(data["period"], "data.period")
    instruments_raw = data["instruments"]
    if not isinstance(instruments_raw, list) or not instruments_raw:
        raise ConfigError("data.instruments must be a non-empty list")
    instruments = tuple(
        _load_instrument(item, index) for index, item in enumerate(instruments_raw)
    )
    codes = [item.code for item in instruments]
    duplicate_codes = sorted({code for code in codes if codes.count(code) > 1})
    if duplicate_codes:
        raise ConfigError(f"duplicate instrument code(s): {', '.join(duplicate_codes)}")

    return DataConfig(
        provider=provider,
        provider_options=dict(options),
        start=start,
        end=end,
        period=period,
        instruments=instruments,
    )


def _load_instrument(raw: Any, index: int) -> Instrument:
    path = f"data.instruments[{index}]"
    item = _require_mapping(raw, path)
    _check_keys(item, {"code", "kind"}, {"code", "kind"}, path)
    code = item["code"]
    if not isinstance(code, str) or not code.strip():
        raise ConfigError(f"{path}.code must be a non-empty string")
    kind_raw = item["kind"]
    if not isinstance(kind_raw, str):
        raise ConfigError(f"{path}.kind must be 'stock' or 'sector_index'")
    kind_aliases = {
        "stock": "stock",
        "sector": "sector_index",
        "sector_index": "sector_index",
    }
    try:
        kind = kind_aliases[kind_raw.strip().lower()]
    except KeyError as exc:
        raise ConfigError(f"{path}.kind must be 'stock' or 'sector_index'") from exc
    return Instrument(code=code.strip(), kind=kind)


def _load_rules(raw: Any) -> tuple[Rule, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("rules must be a non-empty list")
    rules: list[Rule] = []
    for index, raw_rule in enumerate(raw):
        path = f"rules[{index}]"
        item = _require_mapping(raw_rule, path)
        _check_keys(item, {"label", "when"}, {"label", "when"}, path)
        label = item["label"]
        if not isinstance(label, str) or not label.strip():
            raise ConfigError(f"{path}.label must be a non-empty string")
        rules.append(
            Rule(
                label=label.strip(),
                when=load_rule_expression(item["when"], f"{path}.when"),
            )
        )
    labels = [rule.label for rule in rules]
    duplicate_labels = sorted({label for label in labels if labels.count(label) > 1})
    if duplicate_labels:
        raise ConfigError(f"duplicate rule label(s): {', '.join(duplicate_labels)}")
    return tuple(rules)


def _load_output(raw: Any, config_dir: Path) -> OutputConfig:
    if raw is None:
        return OutputConfig()
    output = _require_mapping(raw, "output")
    _check_keys(output, {"format", "path", "include_matches"}, set(), "output")
    output_format = output.get("format", "json")
    if not isinstance(output_format, str) or output_format not in {"json", "markdown"}:
        raise ConfigError("output.format must be 'json' or 'markdown'")
    raw_path = output.get("path")
    if raw_path is not None and (not isinstance(raw_path, str) or not raw_path.strip()):
        raise ConfigError("output.path must be a non-empty string")
    output_path = None
    if raw_path:
        candidate = Path(raw_path)
        output_path = candidate if candidate.is_absolute() else config_dir / candidate
    include_matches = output.get("include_matches", False)
    if not isinstance(include_matches, bool):
        raise ConfigError("output.include_matches must be true or false")
    return OutputConfig(
        format=output_format,
        path=output_path,
        include_matches=include_matches,
    )


def _parse_date(value: Any, path: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(f"{path} must use YYYY-MM-DD format") from exc
    raise ConfigError(f"{path} must be a date in YYYY-MM-DD format")


def _parse_period(value: Any, path: str):
    if not isinstance(value, str):
        raise ConfigError(f"{path} must be 'day' or '120m'")
    aliases = {
        "day": "day",
        "k_day": "day",
        "120m": "120m",
        "k_120m": "120m",
    }
    try:
        return aliases[value.strip().lower()]
    except KeyError as exc:
        raise ConfigError(f"{path} must be 'day' or '120m'") from exc


def _validate_futu_options(options: Mapping[str, Any]) -> None:
    allowed = {"host", "port", "max_count", "page_delay_seconds"}
    unknown = set(options) - allowed
    if unknown:
        raise ConfigError(
            "data.provider_options contains unknown Futu option(s): "
            + ", ".join(sorted(unknown))
        )
    if "host" in options and (
        not isinstance(options["host"], str) or not options["host"].strip()
    ):
        raise ConfigError("data.provider_options.host must be a non-empty string")
    if "port" in options and (
        type(options["port"]) is not int or not 1 <= options["port"] <= 65535
    ):
        raise ConfigError("data.provider_options.port must be an integer from 1 to 65535")
    if "max_count" in options and (
        type(options["max_count"]) is not int or not 1 <= options["max_count"] <= 1000
    ):
        raise ConfigError("data.provider_options.max_count must be an integer from 1 to 1000")
    if "page_delay_seconds" in options and (
        isinstance(options["page_delay_seconds"], bool)
        or not isinstance(options["page_delay_seconds"], (int, float))
        or options["page_delay_seconds"] < 0
    ):
        raise ConfigError("data.provider_options.page_delay_seconds must be a non-negative number")


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _check_keys(
    mapping: Mapping[str, Any],
    allowed: set[str],
    required: set[str],
    path: str,
) -> None:
    keys = set(mapping)
    unknown = keys - allowed
    missing = required - keys
    if unknown:
        raise ConfigError(f"{path} contains unknown key(s): {', '.join(sorted(map(str, unknown)))}")
    if missing:
        raise ConfigError(f"{path} is missing key(s): {', '.join(sorted(missing))}")
