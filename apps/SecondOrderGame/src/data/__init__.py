"""Public market-data boundary and implementations."""

from .akshare_client import (
    AkShareApiError,
    AkShareClient,
    AkShareMarketDataSource,
)
from .fake_client import FakeMarketDataSource
from .futu_client import FutuApiError, FutuClient, FutuMarketDataSource
from .capital_flow_ledger import (
    MAX_CODES_PER_SCOPE,
    MAX_WINDOW_DAYS,
    MIN_WINDOW_DAYS,
    CapitalFlowCollectionReport,
    CapitalFlowCollector,
    CapitalFlowFailure,
    CapitalFlowLedger,
)
from .daily_cache import (
    DailyMaterialArchiveError,
    DailyMaterialCache,
    DailyMaterialCacheClosedError,
    DailyMaterialSnapshot,
)
from .models import Bar, CapitalFlow, DragonTiger, LimitPoolRecord, NewsItem, ScoredNewsEvent
from .news_sentiment import NewsSentimentAnalyzer
from .news_prefetch import (
    NEWS_CACHE_CATEGORY,
    NewsPrefetchFailure,
    NewsPrefetchReport,
    NewsPrefetchTask,
)
from .protocol import DataSourceError, MarketDataSource
from .rate_limiter import FakeClock, RateLimiter, RateLimitExceeded
from .sentiment_ledger import SentimentLedger, SentimentLedgerError, SentimentState
from .sentiment_calculator import SentimentCalculator, SentimentIndexResult
from .sentiment_breadth import SentimentBreadth, SentimentBreadthCalculator
from .tavily_client import TAVILY_SEARCH_ENDPOINT, TavilyNewsProvider

__all__ = [
    "AkShareApiError",
    "AkShareClient",
    "AkShareMarketDataSource",
    "Bar",
    "CapitalFlowCollectionReport",
    "CapitalFlowCollector",
    "CapitalFlowFailure",
    "CapitalFlowLedger",
    "CapitalFlow",
    "DataSourceError",
    "DailyMaterialArchiveError",
    "DailyMaterialCache",
    "DailyMaterialCacheClosedError",
    "DailyMaterialSnapshot",
    "DragonTiger",
    "LimitPoolRecord",
    "FakeClock",
    "FakeMarketDataSource",
    "FutuApiError",
    "FutuClient",
    "FutuMarketDataSource",
    "MarketDataSource",
    "MAX_CODES_PER_SCOPE",
    "MAX_WINDOW_DAYS",
    "MIN_WINDOW_DAYS",
    "NEWS_CACHE_CATEGORY",
    "TAVILY_SEARCH_ENDPOINT",
    "NewsItem",
    "ScoredNewsEvent",
    "NewsSentimentAnalyzer",
    "NewsPrefetchFailure",
    "NewsPrefetchReport",
    "NewsPrefetchTask",
    "RateLimiter",
    "RateLimitExceeded",
    "SentimentLedger",
    "SentimentLedgerError",
    "SentimentState",
    "SentimentCalculator",
    "SentimentIndexResult",
    "SentimentBreadth",
    "SentimentBreadthCalculator",
    "TavilyNewsProvider",
]
