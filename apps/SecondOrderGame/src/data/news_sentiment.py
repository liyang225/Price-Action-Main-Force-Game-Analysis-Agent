"""Deterministic, auditable scoring of prefetched sector news."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
import hashlib
import math
from pathlib import Path
import re
from typing import Any

import yaml

from src.config_validator import validate_sentiment
from src.data.models import ModelNewsJudgment, NewsItem, ScoredNewsEvent


ROOT = Path(__file__).resolve().parents[2]
_AUTHORITATIVE = ("交易所", "证监会", "国务院", "发改委", "财政部", "工信部", "公司公告")
_MAJOR_MEDIA = ("新华社", "证券时报", "上海证券报", "中国证券报", "财联社", "第一财经")
_POSITIVE = ("利好", "增长", "回升", "支持", "突破", "上调", "中标", "扩产", "改善", "增持")
_NEGATIVE = ("利空", "下降", "下滑", "减持", "处罚", "调查", "风险", "亏损", "暴跌", "裁员")
_NEGATIVE_CONTEXT = ("不及预期", "低于预期", "未达预期", "增长放缓", "同比转降", "由盈转亏")
_DISTRIBUTION_PURPOSE = ("出货", "减持", "套现", "派发")


class NewsSentimentAnalyzer:
    """Turn provider-neutral news into weighted, deduplicated events."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        copied = dict(config)
        validate_sentiment(copied)
        self._config = copied["news_scoring"]

    @classmethod
    def from_file(
        cls, path: Path | str = ROOT / "config" / "sentiment.yaml"
    ) -> "NewsSentimentAnalyzer":
        try:
            config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"无法加载情绪指数配置: {exc}") from exc
        if not isinstance(config, Mapping):
            raise ValueError("情绪指数配置顶层必须是映射")
        return cls(config)

    def analyze(
        self,
        news_items: Sequence[NewsItem | Mapping[str, Any]],
        *,
        sector_code: str,
        sector_name: str,
        target_security: str | None,
        as_of: datetime,
        subject_purpose: Mapping[str, Any] | None = None,
        model_judgments: Sequence[ModelNewsJudgment] | None = None,
    ) -> tuple[ScoredNewsEvent, ...]:
        if not sector_code.strip() or not sector_name.strip():
            raise ValueError("sector_code and sector_name must be non-empty strings")
        if not isinstance(as_of, datetime):
            raise TypeError("as_of must be a datetime")
        judgments = tuple(model_judgments) if model_judgments is not None else None
        purpose = str((subject_purpose or {}).get("true_purpose") or "未分析")
        selected: dict[str, ScoredNewsEvent] = {}
        for index, item in enumerate(news_items):
            judgment = judgments[index] if judgments is not None and index < len(judgments) else None
            event = self._score_one(
                item,
                sector_code=sector_code,
                target_security=target_security,
                as_of=as_of,
                subject_purpose=purpose,
                judgment=judgment,
            )
            previous = selected.get(event.event_fingerprint)
            if previous is None or _evidence_weight(event) > _evidence_weight(previous):
                selected[event.event_fingerprint] = event
        return tuple(selected.values())

    def _score_one(
        self,
        item: NewsItem | Mapping[str, Any],
        *,
        sector_code: str,
        target_security: str | None,
        as_of: datetime,
        subject_purpose: str,
        judgment: ModelNewsJudgment | None = None,
    ) -> ScoredNewsEvent:
        raw = asdict(item) if is_dataclass(item) and not isinstance(item, type) else dict(item)
        title = str(raw.get("title") or "").strip()
        snippet = str(raw.get("snippet") or "").strip()
        published = str(raw.get("published_date") or "").strip()
        source = str(raw.get("source") or "").strip()
        related_raw = raw.get("related_securities") or ()
        related = (related_raw,) if isinstance(related_raw, str) else tuple(str(value) for value in related_raw)
        relevance = (
            _unit_factor(judgment.relevance)
            if judgment is not None and judgment.relevance is not None
            else self._relevance(related, sector_code, target_security)
        )
        validity = self._validity(published, as_of)
        credibility = (
            _unit_factor(judgment.credibility)
            if judgment is not None and judgment.credibility is not None
            else self._credibility(source)
        )
        explicit = raw.get("sentiment_score")
        if judgment is not None:
            surface_score = _finite_score(judgment.sentiment_score)
        elif explicit is None:
            surface_score = _lexical_score(f"{title} {snippet}")
        else:
            surface_score = _finite_score(explicit)
        purpose_score = _apply_subject_purpose(surface_score, subject_purpose)
        score = _clamp(purpose_score * relevance * validity * credibility, -8.0, 8.0)
        return ScoredNewsEvent(
            title=title,
            snippet=snippet,
            url=str(raw.get("url") or ""),
            published_date=published,
            source=source,
            related_securities=related,
            relevance=round(relevance, 6),
            validity=round(validity, 6),
            source_credibility=round(credibility, 6),
            sentiment_score=round(score, 6),
            subject_purpose=subject_purpose,
            event_fingerprint=_fingerprint(title, snippet),
        )

    def _relevance(
        self, related: tuple[str, ...], sector_code: str, target_security: str | None
    ) -> float:
        if not related:
            return float(self._config["default_relevance"])
        normalized = {_normalize_security(value) for value in related}
        targets = {_normalize_security(sector_code)}
        if target_security:
            targets.add(_normalize_security(target_security))
        return 1.0 if normalized & targets else float(self._config["unrelated_relevance"])

    def _validity(self, published: str, as_of: datetime) -> float:
        published_at = _parse_datetime(published, reference=as_of)
        if published_at is None:
            return 0.0
        comparison = as_of
        if comparison.tzinfo is not None:
            comparison = comparison.replace(tzinfo=None)
        age_days = (comparison - published_at).total_seconds() / 86400.0
        if age_days < 0:
            return 0.0
        if age_days > float(self._config["max_age_days"]):
            return 0.0
        return 0.5 ** (age_days / float(self._config["half_life_days"]))

    def _credibility(self, source: str) -> float:
        if any(keyword in source for keyword in _AUTHORITATIVE):
            return float(self._config["authoritative_source_credibility"])
        if any(keyword in source for keyword in _MAJOR_MEDIA):
            return float(self._config["major_media_credibility"])
        return float(self._config["default_source_credibility"])


def _lexical_score(text: str) -> float:
    negative_context = sum(1 for phrase in _NEGATIVE_CONTEXT if phrase in text)
    positive = sum(1 for word in _POSITIVE if word in text)
    negative = sum(1 for word in _NEGATIVE if word in text) + negative_context * 2
    return _clamp((positive - negative) / 3.0 * 8.0, -8.0, 8.0)


def _apply_subject_purpose(score: float, purpose: str) -> float:
    if score > 0 and any(word in purpose for word in _DISTRIBUTION_PURPOSE):
        return -score * 0.75
    return score


def _fingerprint(title: str, snippet: str) -> str:
    basis = title or snippet[:120]
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", basis).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _parse_datetime(value: object, *, reference: datetime | None = None) -> datetime | None:
    """Parse provider timestamps in the formats Futu/Tavily actually emit.

    Futu's ``publish_time`` is an opaque string (e.g. ``5/13`` or ``5/13 14:30``)
    with no year; Tavily emits ISO-8601.  Unyearful values borrow the reference
    year so freshness scoring never collapses to zero on a format mismatch.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        timestamp = float(value)
        if timestamp > 1e12:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed

    # RFC 2822 (Tavily emits e.g. "Sat, 15 Aug 2026 00:00:00 GMT").  A failed
    # parse must not collapse freshness to zero, so this runs before the
    # strptime fallbacks below and only returns on a genuine match.
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is not None:
        return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed

    year = reference.year if reference is not None else datetime.now().year
    for fmt in (
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    for fmt in ("%m/%d %H:%M", "%m/%d"):
        try:
            return datetime.strptime(f"{year}/{text}", f"%Y/{fmt}")
        except ValueError:
            continue
    return None


def _normalize_security(value: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", value.upper())


def _finite_score(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError("news sentiment_score must be a finite number")
    return _clamp(float(value), -8.0, 8.0)


def _unit_factor(value: Any) -> float:
    """Clamp a soft factor (relevance / credibility) to [0, 1]."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return 0.0
    return _clamp(float(value), 0.0, 1.0)


def _evidence_weight(event: ScoredNewsEvent) -> float:
    return event.relevance * event.validity * event.source_credibility


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


__all__ = ["NewsSentimentAnalyzer", "ScoredNewsEvent"]
