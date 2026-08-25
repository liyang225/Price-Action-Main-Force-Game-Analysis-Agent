from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping

if TYPE_CHECKING:
    from .rules import Rule


Period = Literal["day", "120m"]
InstrumentKind = Literal["stock", "sector_index"]
OutputFormat = Literal["json", "markdown"]


@dataclass(frozen=True)
class Instrument:
    code: str
    kind: InstrumentKind


@dataclass(frozen=True)
class HistoryRequest:
    code: str
    kind: InstrumentKind
    period: Period
    start: date
    end: date


@dataclass(frozen=True)
class DataConfig:
    provider: str
    provider_options: Mapping[str, Any]
    start: date
    end: date
    period: Period
    instruments: tuple[Instrument, ...]


@dataclass(frozen=True)
class OutputConfig:
    format: OutputFormat = "json"
    path: Path | None = None
    include_matches: bool = False


@dataclass(frozen=True)
class ResearchConfig:
    version: int
    data: DataConfig
    rules: tuple[Rule, ...]
    output: OutputConfig = field(default_factory=OutputConfig)
