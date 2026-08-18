"""Refresh interval, cache TTL, and zombie-loop timing for data sources."""
from __future__ import annotations

HTTP_POLL_SOURCES: frozenset[str] = frozenset({"eastmoney", "akshare"})

HTTP_MIN_REFRESH_MS = 2500
HTTP_SNAPSHOT_CACHE_TTL_S = 8.0
HTTP_SNAPSHOT_CACHE_TTL_1D_S = 12.0
HTTP_ZOMBIE_JOIN_MS = 15_000
DEFAULT_ZOMBIE_JOIN_MS = 5000


def is_http_poll_source(kind: str) -> bool:
    return kind in HTTP_POLL_SOURCES


def effective_refresh_interval_ms(
    kind: str,
    user_ms: int,
    *,
    timeframe: str = "",
) -> int:
    """Clamp user refresh interval for slow HTTP/Baostock sources."""
    ms = max(500, int(user_ms or 1000))
    if kind in HTTP_POLL_SOURCES:
        ms = max(ms, HTTP_MIN_REFRESH_MS)
    if kind in HTTP_POLL_SOURCES and timeframe == "1d":
        ms = max(ms, 3000)
    return ms


def snapshot_cache_ttl_s(timeframe: str) -> float:
    if timeframe in ("1d", "1w", "1M"):
        return HTTP_SNAPSHOT_CACHE_TTL_1D_S
    if timeframe == "1m":
        return 4.0
    return HTTP_SNAPSHOT_CACHE_TTL_S


def zombie_join_timeout_ms(kind: str) -> int:
    if kind in HTTP_POLL_SOURCES:
        return HTTP_ZOMBIE_JOIN_MS
    return DEFAULT_ZOMBIE_JOIN_MS
