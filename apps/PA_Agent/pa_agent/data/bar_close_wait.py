"""Helpers for waiting until the current forming bar closes."""
from __future__ import annotations

import math
import re
import time
from datetime import datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo

from pa_agent.data.base import KlineBar

_TIMEFRAME_SECONDS_RE = re.compile(r"^(\d+)([mhdw])$", re.IGNORECASE)

# Month uses uppercase M in MT5; UI combos use lowercase units only.
_TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}
_CN_TZ = ZoneInfo("Asia/Shanghai")


def timeframe_to_seconds(timeframe: str) -> int | None:
    """Map timeframe string (e.g. ``5m``, ``1h``) to bar duration in seconds."""
    tf = str(timeframe or "").strip()
    if not tf:
        return None
    if tf in _TIMEFRAME_SECONDS:
        return _TIMEFRAME_SECONDS[tf]
    m = _TIMEFRAME_SECONDS_RE.match(tf)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "m":
        return n * 60
    if unit == "h":
        return n * 3600
    if unit == "d":
        return n * 86400
    if unit == "w":
        return n * 7 * 86400
    return None


def seconds_until_bar_closes(
    ts_open_ms: int,
    timeframe: str,
    *,
    now_ms: int | None = None,
    symbol: str | None = None,
    timestamp_is_close: bool = False,
) -> int | None:
    """Whole seconds until a bar closes from its opening time or close label."""
    duration_s = timeframe_to_seconds(timeframe)
    if duration_s is None:
        return None
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if timestamp_is_close:
        return max(0, int(math.ceil((int(ts_open_ms) - int(now_ms)) / 1000)))
    if _looks_like_ashare_symbol(symbol):
        close_ms = int(ts_open_ms) + duration_s * 1000
        return max(0, int(math.ceil((close_ms - int(now_ms)) / 1000)))
    # NOTE:
    # Some data sources provide ``ts_open`` with a fixed timezone/base offset.
    # Using absolute ``close_ms = ts_open + duration`` would then make the
    # countdown drift by that whole offset (e.g. ~8h).
    # Instead, compute remaining time within the duration window by taking
    # elapsed % duration. This is robust to constant offsets.
    duration_ms = duration_s * 1000
    elapsed_ms = int(now_ms) - int(ts_open_ms)
    if elapsed_ms == 0:
        return duration_s

    # remainder in [0, duration_ms)
    remainder_ms = elapsed_ms % duration_ms
    if remainder_ms == 0:
        # now exactly on a boundary:
        # - elapsed > 0 → bar already closed
        # - elapsed < 0 → bar "would close" a full duration away (offset case)
        return 0 if elapsed_ms > 0 else duration_s

    remaining_ms = duration_ms - remainder_ms
    return int(math.ceil(remaining_ms / 1000))


def next_ashare_session_open_ms(
    timeframe: str,
    *,
    symbol: str | None = None,
    now_ms: int | None = None,
) -> int | None:
    """Return the next A-share intraday session open when the market is closed.

    ``None`` means the market is currently in a continuous trading session.
    The caller can use the returned open time together with the selected
    timeframe to wait for the first fully closed bar after a break.
    """
    duration_s = timeframe_to_seconds(timeframe)
    if (
        not _looks_like_ashare_symbol(symbol)
        or duration_s is None
        or duration_s >= 24 * 60 * 60
    ):
        return None
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    now = datetime.fromtimestamp(int(now_ms) / 1000, tz=_CN_TZ)
    trade_date = now.date()

    def session_open(day) -> int:
        return int(
            datetime.combine(day, clock_time(9, 30), tzinfo=_CN_TZ).timestamp() * 1000
        )

    def next_weekday(day):
        candidate = day + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    if now.weekday() >= 5:
        while trade_date.weekday() >= 5:
            trade_date += timedelta(days=1)
        return session_open(trade_date)

    morning_open = datetime.combine(trade_date, clock_time(9, 30), tzinfo=_CN_TZ)
    morning_close = datetime.combine(trade_date, clock_time(11, 30), tzinfo=_CN_TZ)
    afternoon_open = datetime.combine(trade_date, clock_time(13, 0), tzinfo=_CN_TZ)
    afternoon_close = datetime.combine(trade_date, clock_time(15, 0), tzinfo=_CN_TZ)
    if now < morning_open:
        return int(morning_open.timestamp() * 1000)
    if morning_open <= now < morning_close or afternoon_open <= now < afternoon_close:
        return None
    if morning_close <= now < afternoon_open:
        return int(afternoon_open.timestamp() * 1000)
    return session_open(next_weekday(trade_date))


def reference_now_ms(
    *,
    now_ms: int | None = None,
    data_source: object | None = None,
) -> int:
    """Wall-clock for forming-bar checks: broker/server time when available, else local.

    When the broker server time lags significantly behind local wall-clock time
    (e.g. over 60 seconds), it means the broker is not sending new ticks
    (market halted / weekend). In that case, fall back to local time so that
    ``is_bar_still_forming`` correctly returns False for stale forming bars.
    """
    if now_ms is not None:
        return int(now_ms)
    local_ms = int(time.time() * 1000)
    if data_source is not None:
        server_time_ms = getattr(data_source, "server_time_ms", None)
        if callable(server_time_ms):
            t = server_time_ms()
            if t is not None:
                server_ms = int(t)
                # If broker time is more than 60 s behind local time, the broker
                # is not sending ticks (weekend / halt). Use local time so that
                # expired bars are not mistakenly treated as still forming.
                if local_ms - server_ms < 60_000:
                    return server_ms
                # Broker time is stale — fall through to local time
    return local_ms


def _looks_like_ashare_symbol(symbol: str | None) -> bool:
    from pa_agent.data.market_defaults import normalize_ashare_tv_code

    code = normalize_ashare_tv_code(symbol or "")
    return len(code) == 6 and code.isdigit()


def is_bar_still_forming(
    bar: KlineBar,
    timeframe: str,
    *,
    now_ms: int | None = None,
    symbol: str | None = None,
) -> bool:
    """True when the newest bar period has not ended (wall-clock + A-share daily session)."""
    if bar.closed:
        return False
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    tf = str(timeframe or "").strip().lower()
    if tf == "1d" and _looks_like_ashare_symbol(symbol):
        try:
            from pa_agent.data.akshare_source import _ashare_session_open

            if not _ashare_session_open():
                return False
        except ImportError:
            pass
    duration_s = timeframe_to_seconds(timeframe)
    if duration_s is None:
        return True
    from pa_agent.data.datetime_ts import ts_open_to_ms

    ts_open = int(ts_open_to_ms(bar.ts_open))
    close_ms = ts_open if getattr(bar, "timestamp_is_close", False) else (
        ts_open + duration_s * 1000
    )

    # Primary check: use the provided now_ms (broker or local time).
    if int(now_ms) >= close_ms:
        return False

    # Safety net for daily/weekly bars: broker server time may be stale (no
    # ticks during weekend / halt), causing now_ms to lag behind real time.
    # If local wall-clock time is more than duration + 6 h past ts_open,
    # the bar has definitely closed regardless of broker time.
    if tf in ("1d", "1w"):
        local_ms = int(time.time() * 1000)
        # 6 h safety margin covers all known broker UTC offsets (UTC-5 to UTC+5).
        safety_ms = 6 * 3600 * 1000
        if local_ms >= close_ms + safety_ms:
            return False

    return True


def has_forming_bar_at_head(
    bars_newest_first: list[KlineBar],
    timeframe: str | None = None,
    *,
    now_ms: int | None = None,
    symbol: str | None = None,
) -> bool:
    """True when index 0 is a real forming bar (not a stale unclosed flag after halt)."""
    if not bars_newest_first:
        return False
    if not timeframe:
        return not bars_newest_first[0].closed
    return is_bar_still_forming(
        bars_newest_first[0],
        timeframe,
        now_ms=now_ms,
        symbol=symbol,
    )


def current_forming_ts(
    bars_newest_first: list[KlineBar],
    timeframe: str | None = None,
    *,
    symbol: str | None = None,
    now_ms: int | None = None,
) -> int | None:
    """Return ts_open of the newest forming bar, or None if head bar is already closed."""
    if not has_forming_bar_at_head(
        bars_newest_first, timeframe, now_ms=now_ms, symbol=symbol
    ):
        return None
    return int(bars_newest_first[0].ts_open)


def forming_bar_has_closed(
    waited_ts_open: int,
    bars_newest_first: list[KlineBar],
    timeframe: str | None = None,
    *,
    symbol: str | None = None,
    now_ms: int | None = None,
    timestamp_is_close: bool = False,
) -> bool:
    """True when the waited bar finished (new bar appeared or head is no longer forming)."""
    duration_s = timeframe_to_seconds(timeframe) if timeframe else None
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    close_ms = int(waited_ts_open) + (0 if timestamp_is_close else duration_s or 0) * 1000
    if (
        _looks_like_ashare_symbol(symbol)
        and duration_s is not None
        and int(now_ms) < close_ms
    ):
        return False
    if not bars_newest_first:
        # bars 为空 (数据拉取失败): 用时间判断是否已过收盘时间, 避免倒计时走完却不提交
        if duration_s is not None:
            return int(now_ms) >= close_ms
        return False
    if not has_forming_bar_at_head(
        bars_newest_first, timeframe, now_ms=now_ms, symbol=symbol
    ):
        return True
    return int(bars_newest_first[0].ts_open) != int(waited_ts_open)
