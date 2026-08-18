"""Horizontal aggregation of registered sector sentiment."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median

from src.data.sentiment_ledger import SentimentState


@dataclass(frozen=True, slots=True)
class SentimentBreadth:
    status: str
    sector_count: int
    covered_sector_count: int
    median_sentiment: float | None
    climax_ratio: float | None
    missing_sector_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "sector_count": self.sector_count,
            "covered_sector_count": self.covered_sector_count,
            "median_sentiment": self.median_sentiment,
            "climax_ratio": self.climax_ratio,
            "missing_sector_codes": list(self.missing_sector_codes),
        }


class SentimentBreadthCalculator:
    def calculate(
        self,
        states: Sequence[SentimentState],
        *,
        registered_sector_codes: Sequence[str],
        cycle_positions: Mapping[str, str],
        registry_complete: bool = True,
    ) -> SentimentBreadth:
        registered = tuple(dict.fromkeys(code.strip() for code in registered_sector_codes if code.strip()))
        values = {state.sector_code: state.sentiment_index for state in states}
        missing = tuple(code for code in registered if code not in values or code not in cycle_positions)
        covered = tuple(code for code in registered if code in values)
        complete = bool(registered) and registry_complete and not missing
        return SentimentBreadth(
            status="complete" if complete else "partial" if registered else "insufficient_data",
            sector_count=len(registered),
            covered_sector_count=len(covered),
            median_sentiment=float(median(values[code] for code in covered)) if covered else None,
            climax_ratio=(
                sum(cycle_positions[code] == "高潮" for code in registered) / len(registered)
                if complete
                else None
            ),
            missing_sector_codes=missing,
        )


__all__ = ["SentimentBreadth", "SentimentBreadthCalculator"]
