"""Deterministic policy-environment detection from market and text evidence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from numbers import Real
from pathlib import Path
from statistics import median
from types import MappingProxyType
from typing import Any, Literal

import yaml

from src.data.daily_cache import DailyMaterialSnapshot
from src.data.models import Bar, NewsItem
from src.data.news_prefetch import NEWS_CACHE_CATEGORY
from src.data.protocol import DataSourceError, MarketDataSource


PolicyEnvironment = Literal["无干预", "政策暖风", "国家队托底中", "政策打压"]
POLICY_ENVIRONMENTS: tuple[PolicyEnvironment, ...] = (
    "无干预",
    "政策暖风",
    "国家队托底中",
    "政策打压",
)

# 原始新闻缓存类别：软信号直接扫描该类别下各板块新闻的
# title + snippet 文本（NEWS_CACHE_CATEGORY 定义于 news_prefetch）。
# 每条缓存值可能是 NewsItem、str、决策包装 dict（{status, items, ...}）。
_NEWS_KEY = NEWS_CACHE_CATEGORY


@dataclass(frozen=True, slots=True)
class VerifiedETF:
    """A caller-attested broad-market ETF identifier; no code is guessed."""

    code: str
    benchmark: Literal["沪深300", "上证50"]

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("verified ETF code must be a non-empty string")
        if self.benchmark not in ("沪深300", "上证50"):
            raise ValueError("verified ETF benchmark must be 沪深300 or 上证50")


@dataclass(frozen=True, slots=True)
class PolicySoftRule:
    """One configured text-evidence rule for a non-neutral policy environment."""

    environment: Literal["政策暖风", "国家队托底中", "政策打压"]
    keywords: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.environment not in POLICY_ENVIRONMENTS[1:]:
            raise ValueError("soft-rule environment must be non-neutral")
        if not isinstance(self.keywords, tuple) or not self.keywords:
            raise ValueError("soft-rule keywords must be a non-empty tuple")
        if not all(isinstance(keyword, str) and keyword.strip() for keyword in self.keywords):
            raise ValueError("soft-rule keywords must be non-empty strings")


@dataclass(frozen=True, slots=True)
class PolicyDetectorConfig:
    """Explicit detector inputs; soft-rule order is conflict precedence."""

    etfs: tuple[VerifiedETF, ...]
    volume_lookback_bars: int
    abnormal_volume_ratio: float
    minimum_confirmations: int
    history_calendar_days: int
    ktype: str
    soft_rules: tuple[PolicySoftRule, ...]
    policy_news_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.etfs, tuple) or not all(
            isinstance(etf, VerifiedETF) for etf in self.etfs
        ):
            raise TypeError("etfs must be a tuple of VerifiedETF values")
        codes = [etf.code for etf in self.etfs]
        if len(codes) != len(set(codes)):
            raise ValueError("verified ETF codes must be unique")
        if (
            isinstance(self.volume_lookback_bars, bool)
            or not isinstance(self.volume_lookback_bars, int)
            or self.volume_lookback_bars < 2
        ):
            raise ValueError("volume_lookback_bars must be an integer of at least 2")
        if (
            isinstance(self.abnormal_volume_ratio, bool)
            or not isinstance(self.abnormal_volume_ratio, Real)
            or self.abnormal_volume_ratio <= 1
        ):
            raise ValueError("abnormal_volume_ratio must be greater than 1")
        if (
            isinstance(self.minimum_confirmations, bool)
            or not isinstance(self.minimum_confirmations, int)
            or self.minimum_confirmations < 1
        ):
            raise ValueError("minimum_confirmations must be a positive integer")
        if self.etfs and self.minimum_confirmations > len(self.etfs):
            raise ValueError("minimum_confirmations cannot exceed configured ETFs")
        if (
            isinstance(self.history_calendar_days, bool)
            or not isinstance(self.history_calendar_days, int)
            or self.history_calendar_days < 1
        ):
            raise ValueError("history_calendar_days must be a positive integer")
        if not isinstance(self.ktype, str) or not self.ktype.strip():
            raise ValueError("ktype must be a non-empty string")
        if not isinstance(self.soft_rules, tuple) or not all(
            isinstance(rule, PolicySoftRule) for rule in self.soft_rules
        ):
            raise TypeError("soft_rules must be a tuple of PolicySoftRule values")
        rule_environments = tuple(rule.environment for rule in self.soft_rules)
        if set(rule_environments) != set(POLICY_ENVIRONMENTS[1:]) or len(
            rule_environments
        ) != len(set(rule_environments)):
            raise ValueError("soft_rules must define each non-neutral environment once")
        if not isinstance(self.policy_news_keys, tuple):
            raise ValueError("policy_news_keys must be a tuple of strings")
        if not all(
            isinstance(key, str) and key.strip() for key in self.policy_news_keys
        ):
            raise ValueError("policy_news_keys must contain non-empty strings")


@dataclass(frozen=True, slots=True)
class PolicyEvidence:
    channel: Literal["hard", "soft", "system"]
    environment: PolicyEnvironment | None
    summary: str


@dataclass(frozen=True, slots=True)
class PolicyDetection:
    environment: PolicyEnvironment
    multipliers: Mapping[str, float]
    hard_branch_enabled: bool
    soft_branch_enabled: bool
    evidence: tuple[PolicyEvidence, ...]


class PolicyDetector:
    """Select one existing multiplier group without touching HMM state."""

    def __init__(
        self,
        source: MarketDataSource,
        policy_multipliers: Mapping[str, Mapping[str, float]],
        config: PolicyDetectorConfig,
    ) -> None:
        self._source = source
        self._policy_multipliers = _validated_multipliers(policy_multipliers)
        self._config = config

    def detect(
        self,
        snapshot: DailyMaterialSnapshot,
        *,
        user_material: Sequence[str | NewsItem] = (),
    ) -> PolicyDetection:
        if not isinstance(snapshot, DailyMaterialSnapshot):
            raise TypeError("snapshot must be a DailyMaterialSnapshot")

        soft_material = _soft_material(
            snapshot,
            user_material,
            policy_news_keys=self._config.policy_news_keys,
        )
        hard_evidence, hard_confirmations = self._hard_evidence(snapshot)
        soft_evidence = _soft_evidence(soft_material, self._config.soft_rules)
        evidence = [*hard_evidence, *soft_evidence]
        soft_environment = _select_soft_environment(
            soft_evidence, self._config.soft_rules
        )

        if hard_confirmations >= self._config.minimum_confirmations:
            environment: PolicyEnvironment = "国家队托底中"
            if soft_environment not in (None, environment):
                evidence.append(
                    PolicyEvidence(
                        "system",
                        environment,
                        "硬信号优先于冲突的软信号；保留两类证据供审计",
                    )
                )
        else:
            environment = soft_environment or "无干预"

        return PolicyDetection(
            environment=environment,
            multipliers=self._policy_multipliers[environment],
            hard_branch_enabled=bool(self._config.etfs),
            soft_branch_enabled=bool(soft_material),
            evidence=tuple(evidence),
        )

    def _hard_evidence(
        self, snapshot: DailyMaterialSnapshot
    ) -> tuple[list[PolicyEvidence], int]:
        if not self._config.etfs:
            return [], 0

        end = snapshot.trading_date.isoformat()
        start = (
            snapshot.trading_date - timedelta(days=self._config.history_calendar_days)
        ).isoformat()
        evidence: list[PolicyEvidence] = []
        confirmations = 0

        for etf in self._config.etfs:
            try:
                bars = self._source.get_kline(etf.code, self._config.ktype, start, end)
                ratio = _latest_volume_ratio(
                    bars,
                    as_of=end,
                    lookback=self._config.volume_lookback_bars,
                )
            except DataSourceError as exc:
                evidence.append(
                    PolicyEvidence(
                        "system",
                        None,
                        f"{etf.benchmark} {etf.code} 行情不可用：{exc}",
                    )
                )
                continue
            except (TypeError, ValueError) as exc:
                evidence.append(
                    PolicyEvidence(
                        "system",
                        None,
                        f"{etf.benchmark} {etf.code} 硬信号关闭：{exc}",
                    )
                )
                continue

            if ratio >= self._config.abnormal_volume_ratio:
                confirmations += 1
                evidence.append(
                    PolicyEvidence(
                        "hard",
                        "国家队托底中",
                        f"{etf.benchmark} {etf.code} {end} 成交量为历史中位数 "
                        f"{ratio:.2f} 倍，达到异常放量阈值",
                    )
                )

        if 0 < confirmations < self._config.minimum_confirmations:
            evidence.append(
                PolicyEvidence(
                    "system",
                    None,
                    f"异常放量 ETF 为 {confirmations} 只，未达到 "
                    f"{self._config.minimum_confirmations} 只的硬信号确认条件",
                )
            )
        return evidence, confirmations


def load_policy_detector_config(
    path: str | Path,
) -> PolicyDetectorConfig:
    """Load and validate the policy-detector configuration from YAML."""
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"无法加载政策检测配置 {source}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("政策检测配置顶层必须是映射")
    if not isinstance(raw.get("version"), int) or raw["version"] < 1:
        raise ValueError("政策检测配置 version 必须是正整数")

    etf_values = raw.get("etfs")
    if not isinstance(etf_values, list) or not etf_values:
        raise ValueError("政策检测配置缺少 etfs 列表")
    etfs = tuple(
        VerifiedETF(str(item["code"]), str(item["benchmark"]))
        for item in etf_values
        if isinstance(item, Mapping)
        and str(item.get("code") or "").strip()
        and str(item.get("benchmark") or "").strip()
        in ("沪深300", "上证50")
    )
    if not etfs:
        raise ValueError("etfs 必须包含至少一个基准为沪深300或上证50的已验证 ETF")

    soft_values = raw.get("soft_rules")
    if not isinstance(soft_values, Mapping) or not soft_values:
        raise ValueError("政策检测配置缺少 soft_rules")
    soft_rules = tuple(
        PolicySoftRule(
            environment=str(environment),
            keywords=tuple(
                str(keyword).strip()
                for keyword in keywords
                if isinstance(keyword, str) and keyword.strip()
            ),
        )
        for environment, keywords in soft_values.items()
        if environment in POLICY_ENVIRONMENTS[1:]
    )
    if not soft_rules:
        raise ValueError("soft_rules 必须定义非中性政策环境的检测词")

    keys = raw.get("policy_news_keys")
    policy_news_keys = (
        tuple(str(key).strip() for key in keys if isinstance(key, str) and key.strip())
        if isinstance(keys, list)
        else ()
    )

    def _int(name: str, *, minimum: int) -> int:
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"政策检测配置 {name} 必须是 >= {minimum} 的整数")
        return value

    def _float(name: str, *, minimum: float) -> float:
        value = raw.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or float(value) <= minimum
        ):
            raise ValueError(f"政策检测配置 {name} 必须大于 {minimum}")
        return float(value)

    return PolicyDetectorConfig(
        etfs=etfs,
        volume_lookback_bars=_int("volume_lookback_bars", minimum=2),
        abnormal_volume_ratio=_float("abnormal_volume_ratio", minimum=1.0),
        minimum_confirmations=_int("minimum_confirmations", minimum=1),
        history_calendar_days=_int("history_calendar_days", minimum=1),
        ktype=str(raw.get("ktype") or "K_DAY").strip() or "K_DAY",
        soft_rules=soft_rules,
        policy_news_keys=policy_news_keys,
    )


def build_policy_detector(
    source: MarketDataSource,
    *,
    config_path: str | Path | None = None,
    hmm_prior_path: str | Path | None = None,
    root: str | Path | None = None,
) -> PolicyDetector:
    """Assemble the production detector from the two config files.

    ``source`` must provide ``get_kline`` for the configured ETFs.  The
    multiplier groups come from ``hmm_prior.yaml`` (主力 participant rows);
    the detector selects one group per environment, and the HMM filter later
    applies the per-participant factor when ``predict_behaviors`` runs.
    """
    from src.hmm_filter import load_config as load_hmm_config

    project_root = (
        Path(root) if root is not None else Path(__file__).resolve().parents[2]
    )
    hmm_config = load_hmm_config(
        hmm_prior_path if hmm_prior_path is not None else project_root / "config" / "hmm_prior.yaml"
    )
    multipliers = hmm_config["policy_multipliers"]["主力"]
    config = load_policy_detector_config(
        config_path if config_path is not None else project_root / "config" / "policy_detector.yaml"
    )
    return PolicyDetector(source, multipliers, config)


def _validated_multipliers(
    policy_multipliers: Mapping[str, Mapping[str, float]],
) -> Mapping[str, Mapping[str, float]]:
    if set(policy_multipliers) != set(POLICY_ENVIRONMENTS):
        raise ValueError("policy_multipliers must define exactly the four policy environments")
    frozen: dict[str, Mapping[str, float]] = {}
    for environment in POLICY_ENVIRONMENTS:
        row = policy_multipliers[environment]
        if not isinstance(row, Mapping) or not row:
            raise ValueError(f"policy_multipliers.{environment} must be a non-empty mapping")
        values: dict[str, float] = {}
        for behavior, multiplier in row.items():
            if (
                not isinstance(behavior, str)
                or not behavior
                or isinstance(multiplier, bool)
                or not isinstance(multiplier, Real)
                or multiplier < 0
            ):
                raise ValueError(f"policy_multipliers.{environment} is invalid")
            values[behavior] = float(multiplier)
        frozen[environment] = MappingProxyType(values)
    return MappingProxyType(frozen)


def _soft_material(
    snapshot: DailyMaterialSnapshot,
    user_material: Sequence[str | NewsItem],
    *,
    policy_news_keys: Sequence[str],
) -> tuple[str | NewsItem, ...]:
    """Collect raw news texts (title + snippet) as soft material.

    The soft signal scans the raw cached news under ``news/<sector>`` and
    matches configured business keywords, not the preanalysis conclusion.
    ``policy_news_keys`` optionally restricts which cached sectors are
    scanned; an empty tuple scans every sector that has news.
    """
    if isinstance(user_material, str | NewsItem) or not isinstance(
        user_material, Sequence
    ):
        raise TypeError("user_material must be a sequence of strings or NewsItem values")
    cached: list[str | NewsItem] = []
    news_by_key = snapshot.materials.get(_NEWS_KEY, {})
    if not isinstance(news_by_key, Mapping):
        raise TypeError(f"{_NEWS_KEY} material must be a mapping keyed by sector")
    for key, items in news_by_key.items():
        if policy_news_keys and str(key) not in policy_news_keys:
            continue
        for item in _flatten_news_items(items):
            text = _material_text(item)
            if text is not None:
                cached.append(text)
    supplied = tuple(user_material)
    if not all(isinstance(item, str | NewsItem) for item in supplied):
        raise TypeError("user_material must contain only strings or NewsItem values")
    return tuple(cached) + supplied


def _flatten_news_items(value: Any) -> tuple[Any, ...]:
    """Flatten one cached news value into NewsItem / str / dict rows.

    The cache may hold a bare row, a sequence of rows, or a decision wrapper
    ``{status, items, ...}`` that nests the actual rows under ``items``.
    """
    if isinstance(value, list | tuple):
        flattened: list[Any] = []
        for item in value:
            flattened.extend(_flatten_news_items(item))
        return tuple(flattened)
    if isinstance(value, Mapping) and isinstance(value.get("items"), list | tuple):
        return _flatten_news_items(value["items"])
    if isinstance(value, NewsItem | str | Mapping):
        return (value,)
    return ()


def _soft_evidence(
    materials: Sequence[str | NewsItem], rules: Sequence[PolicySoftRule]
) -> list[PolicyEvidence]:
    evidence: list[PolicyEvidence] = []
    for material in materials:
        text = _material_text(material)
        for rule in rules:
            matched = next(
                (keyword for keyword in rule.keywords if keyword in text), None
            )
            if matched is not None:
                evidence.append(
                    PolicyEvidence(
                        "soft",
                        rule.environment,
                        f"{_material_summary(material)}（命中：{matched}）",
                    )
                )
                break
    return evidence


def _select_soft_environment(
    evidence: Sequence[PolicyEvidence], rules: Sequence[PolicySoftRule]
) -> PolicyEnvironment | None:
    counts = Counter(
        item.environment for item in evidence if item.environment is not None
    )
    if not counts:
        return None
    precedence = tuple(rule.environment for rule in rules)
    return max(
        precedence,
        key=lambda environment: (
            counts[environment],
            -precedence.index(environment),
        ),
    )


def _latest_volume_ratio(
    bars: Sequence[Bar], *, as_of: str, lookback: int
) -> float:
    try:
        ordered = sorted(
            (bar for bar in bars if bar.time_key[:10] <= as_of),
            key=lambda bar: bar.time_key,
        )
    except (AttributeError, TypeError) as exc:
        raise TypeError("get_kline must return a sequence of Bar values") from exc
    if not all(isinstance(bar, Bar) for bar in ordered):
        raise TypeError("get_kline must return only Bar values")
    if len(ordered) < lookback + 1:
        raise ValueError(f"需要至少 {lookback + 1} 根日 K 线")
    latest = ordered[-1]
    if latest.time_key[:10] != as_of:
        raise ValueError(f"缺少 {as_of} 的已收盘 ETF 日 K 线")
    baseline = median(bar.volume for bar in ordered[-lookback - 1 : -1])
    if baseline <= 0:
        raise ValueError("历史成交量中位数必须为正数")
    if latest.volume < 0:
        raise ValueError("当日成交量不得为负数")
    return latest.volume / baseline


def _material_text(material: str | NewsItem | Mapping[str, Any]) -> str:
    """Return the full searchable text of one news row (never None)."""
    if isinstance(material, str):
        return material
    if isinstance(material, NewsItem):
        return f"{material.title} {material.snippet}"
    if isinstance(material, Mapping):
        title = material.get("title")
        snippet = material.get("snippet")
        if isinstance(title, str) and title.strip():
            return f"{title} {snippet if isinstance(snippet, str) else ''}".strip()
    return ""


def _material_summary(material: str | NewsItem | Mapping[str, Any]) -> str:
    """Return a short human-readable summary of one news row."""
    if isinstance(material, NewsItem):
        return material.title
    if isinstance(material, str):
        return material
    if isinstance(material, Mapping):
        title = material.get("title")
        if isinstance(title, str) and title.strip():
            return title
        return "新闻条目"
    return str(material)
