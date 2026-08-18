"""Provider-neutral market data records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Bar:
    time_key: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float


@dataclass(frozen=True, slots=True)
class CapitalFlow:
    date: str
    code: str
    super_in_flow: float
    big_in_flow: float
    mid_in_flow: float
    sml_in_flow: float
    main_in_flow: float


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """The small snapshot surface used to rank a sector's constituents."""

    code: str
    change_rate: float


@dataclass(frozen=True, slots=True)
class NewsItem:
    title: str
    snippet: str
    url: str
    published_date: str
    source: str = ""
    related_securities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScoredNewsEvent:
    """One deduplicated news event after decision-time preanalysis."""

    title: str
    snippet: str
    url: str
    published_date: str
    source: str
    related_securities: tuple[str, ...]
    relevance: float
    validity: float
    source_credibility: float
    sentiment_score: float
    subject_purpose: str
    event_fingerprint: str


@dataclass(frozen=True, slots=True)
class ModelNewsJudgment:
    """LLM-supplied per-news judgment: signed strength plus the soft factors.

    ``relevance`` and ``credibility`` may be ``None`` when the model does not
    provide them, in which case ``NewsSentimentAnalyzer`` falls back to its
    deterministic rules.
    """

    sentiment_score: float
    relevance: float | None = None
    credibility: float | None = None


@dataclass(frozen=True, slots=True)
class DragonTiger:
    date: str
    code: str
    reason: str
    net_buy_amount: float
    # Optional fields keep the original four-field business seam compatible
    # while preserving the seat-level evidence needed by P4 signals.
    buy_amount: float | None = None
    sell_amount: float | None = None
    institution_net_buy: float | None = None
    institution_net_sell: float | None = None
    hot_money_net_buy: float | None = None
    hot_money_net_sell: float | None = None
    institution_seats: tuple[str, ...] = ()
    hot_money_seats: tuple[str, ...] = ()
    buy_seats: tuple[str, ...] = ()
    sell_seats: tuple[str, ...] = ()
    source: str = ""
    source_reference: str = ""

    @property
    def institution_net_buy_amount(self) -> float | None:
        return self.institution_net_buy

    @property
    def institution_net_sell_amount(self) -> float | None:
        return self.institution_net_sell

    @property
    def hot_money_net_buy_amount(self) -> float | None:
        return self.hot_money_net_buy

    @property
    def hot_money_net_sell_amount(self) -> float | None:
        return self.hot_money_net_sell


@dataclass(frozen=True, slots=True)
class LimitPoolRecord:
    """One stock's daily limit-pool evidence, independent of sector naming."""

    date: str
    code: str
    limit_streak: int
    direction: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("code must be a non-empty string")
        if isinstance(self.limit_streak, bool) or not isinstance(self.limit_streak, int) or self.limit_streak < 1:
            raise ValueError("limit_streak must be a positive integer")
        if self.direction not in {"rise", "fall"}:
            raise ValueError("direction must be 'rise' or 'fall'")
