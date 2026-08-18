"""Deterministic sector sentiment-index calculation from daily materials."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import math
from numbers import Real
from pathlib import Path
from typing import Any

import yaml

from src.config_validator import validate_sentiment
from src.data.models import NewsItem, ScoredNewsEvent


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class SentimentIndexResult:
    sector_code: str
    sentiment_index: float
    previous_index: float
    news_delta: float
    price_action_delta: float
    daily_delta: float
    major_move_suppressed: bool
    daily_return: float
    two_day_return: float
    updated_at: datetime
    status: str = "computed"
    formula: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "sector_code": self.sector_code,
            "sentiment_index": self.sentiment_index,
            "previous_index": self.previous_index,
            "news_delta": self.news_delta,
            "price_action_delta": self.price_action_delta,
            "daily_delta": self.daily_delta,
            "major_move_suppressed": self.major_move_suppressed,
            "daily_return": self.daily_return,
            "two_day_return": self.two_day_return,
            "updated_at": self.updated_at.isoformat(),
            "formula": self.formula,
        }


class SentimentCalculator:
    """Apply ADR-0014 scoring without deriving a cycle state from the index."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        copied = deepcopy(dict(config))
        validate_sentiment(copied)
        self._config = copied

    @property
    def baseline(self) -> float:
        return float(self._config["range"]["baseline"])

    @property
    def config(self) -> dict[str, Any]:
        return deepcopy(self._config)

    def formula_text(self) -> str:
        """Return the auditable scoring formula with the configured parameters."""
        baseline = float(self._config["range"]["baseline"])
        decay = float(self._config["inertia"]["decay"])
        quota = self._config["quota"]
        daily_net = float(quota["daily_net"])
        news_limit = float(quota["news"])
        price_limit = float(quota["price_action"])
        single_news = float(quota["single_news"])
        lines = [
            f"指数 = clamp(基准 {baseline} + (昨日 − {baseline}) × {decay} + 当日净增量, 0, 100)",
            f"当日净增量 = clamp(消息增量 + 行情增量, −{daily_net}, +{daily_net})",
            (
                f"消息增量 = clamp(Σ clamp(情绪分, "
                f"−{single_news}, +{single_news}), −{news_limit}, +{news_limit})"
            ),
            f"行情增量 = clamp(日收益率 × 200, −{price_limit}, +{price_limit})",
            "情绪分 = 大模型评分(±8) × 相关性 × 有效期衰减 × 来源可信度（再按主体目的修正）",
        ]
        return "\n".join(lines)

    @classmethod
    def from_file(cls, path: Path | str = ROOT / "config" / "sentiment.yaml") -> "SentimentCalculator":
        try:
            config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"无法加载情绪指数配置: {exc}") from exc
        if not isinstance(config, Mapping):
            raise ValueError("情绪指数配置顶层必须是映射")
        return cls(config)

    def calculate(
        self,
        *,
        sector_code: str,
        previous_index: float | None,
        news: Sequence[NewsItem | ScoredNewsEvent | Mapping[str, Any]],
        price_action: Mapping[str, Any] | None,
        updated_at: datetime,
    ) -> SentimentIndexResult:
        if not isinstance(sector_code, str) or not sector_code.strip():
            raise ValueError("sector_code must be a non-empty string")
        if not isinstance(updated_at, datetime):
            raise TypeError("updated_at must be a datetime")
        range_ = self._config["range"]
        baseline = float(range_["baseline"])
        lower, upper = float(range_["min"]), float(range_["max"])
        prior = baseline if previous_index is None else _finite(previous_index, "previous_index")
        if not lower <= prior <= upper:
            raise ValueError("previous_index is outside the configured range")

        price = price_action or {}
        if not isinstance(price, Mapping):
            raise TypeError("price_action must be a mapping")
        daily_return = _optional_number(price.get("daily_return"), "daily_return") or 0.0
        two_day_return = _optional_number(price.get("two_day_return"), "two_day_return") or 0.0
        suppression = self._is_major_move(daily_return, two_day_return)

        quota = self._config["quota"]
        news_limit = float(quota["news"])
        if suppression:
            news_limit *= float(self._config["major_move_suppression"]["news_quota_multiplier"])
        single_news_limit = float(quota["single_news"])
        news_delta = _clamp(
            sum(
                _clamp(
                    self._news_score(item),
                    -single_news_limit,
                    single_news_limit,
                )
                for item in news
            ),
            -news_limit,
            news_limit,
        )
        price_delta = _clamp(daily_return * 200.0, -float(quota["price_action"]), float(quota["price_action"]))
        daily_delta = _clamp(
            news_delta + price_delta,
            -float(quota["daily_net"]),
            float(quota["daily_net"]),
        )
        decay = float(self._config["inertia"]["decay"])
        index = _clamp(baseline + (prior - baseline) * decay + daily_delta, lower, upper)
        return SentimentIndexResult(
            sector_code=sector_code.strip(),
            sentiment_index=round(index, 4),
            previous_index=prior,
            news_delta=round(news_delta, 4),
            price_action_delta=round(price_delta, 4),
            daily_delta=round(daily_delta, 4),
            major_move_suppressed=suppression,
            daily_return=round(daily_return, 8),
            two_day_return=round(two_day_return, 8),
            updated_at=updated_at,
            formula=self.formula_text(),
        )

    def hold(
        self,
        *,
        sector_code: str,
        previous_index: float | None,
        updated_at: datetime,
        status: str,
    ) -> SentimentIndexResult:
        prior = self.baseline if previous_index is None else _finite(previous_index, "previous_index")
        return SentimentIndexResult(
            sector_code=sector_code.strip(),
            sentiment_index=round(prior, 4),
            previous_index=prior,
            news_delta=0.0,
            price_action_delta=0.0,
            daily_delta=0.0,
            major_move_suppressed=False,
            daily_return=0.0,
            two_day_return=0.0,
            updated_at=updated_at,
            status=status,
            formula=self.formula_text(),
        )

    def _is_major_move(self, daily_return: float, two_day_return: float) -> bool:
        triggers = self._config["major_move_suppression"]["triggers"]
        return daily_return < float(triggers["single_day_drop"]) or two_day_return < float(triggers["two_day_cumulative"])

    def _news_score(self, item: NewsItem | ScoredNewsEvent | Mapping[str, Any]) -> float:
        if isinstance(item, Mapping):
            explicit = item.get("sentiment_score")
            if explicit is not None:
                return _clamp(_finite(explicit, "news sentiment_score"), -8.0, 8.0)
            text = " ".join(str(item.get(key) or "") for key in ("title", "snippet"))
        elif isinstance(item, NewsItem):
            text = f"{item.title} {item.snippet}"
        elif isinstance(item, ScoredNewsEvent):
            return _clamp(_finite(item.sentiment_score, "news sentiment_score"), -8.0, 8.0)
        else:
            raise TypeError("news items must be NewsItem values or mappings")
        return _lexical_news_score(text)


def _lexical_news_score(text: str) -> float:
    positive = ("利好", "增长", "回升", "支持", "突破", "上调", "中标", "扩产", "改善")
    negative = ("利空", "下降", "下滑", "减持", "处罚", "调查", "风险", "亏损", "暴跌", "裁员")
    score = sum(1 for word in positive if word in text) - sum(1 for word in negative if word in text)
    return _clamp(score / 3.0 * 8.0, -8.0, 8.0)


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _optional_number(value: Any, label: str) -> float | None:
    return None if value is None else _finite(value, label)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


__all__ = ["SentimentCalculator", "SentimentIndexResult"]
