"""Probe whether this machine can reach TradingView via tvdatafeed."""
from __future__ import annotations

import concurrent.futures
import logging
import time

logger = logging.getLogger(__name__)

_DEFAULT_PROBE_TIMEOUT_S = 20.0
_DEFAULT_PROBE_ATTEMPTS = 3
_DEFAULT_RETRY_DELAY_S = 3.0


def _probe_once(*, timeout_s: float) -> tuple[bool, str | None, bool]:
    """Single probe. Returns (ok, failure_detail, retryable)."""

    def _probe() -> None:
        from tvDatafeed import Interval, TvDatafeed  # type: ignore[import]

        tv = TvDatafeed()
        df = tv.get_hist(
            symbol="XAUUSD",
            exchange="OANDA",
            interval=Interval.in_1_minute,
            n_bars=2,
        )
        if df is None or getattr(df, "empty", True):
            raise RuntimeError("TradingView 返回空数据")

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_probe)
            fut.result(timeout=timeout_s)
        return True, None, False
    except concurrent.futures.TimeoutError:
        logger.warning(
            "TradingView connectivity probe timed out after %.0fs", timeout_s
        )
        return False, "连接超时", True
    except ImportError as exc:
        logger.warning(
            "TradingView connectivity probe: tvDatafeed not installed: %s", exc
        )
        return False, str(exc), False
    except Exception as exc:  # noqa: BLE001
        logger.warning("TradingView connectivity probe failed: %s", exc)
        return False, str(exc), True


def check_tradingview_connectivity(
    *,
    timeout_s: float = _DEFAULT_PROBE_TIMEOUT_S,
    max_attempts: int = _DEFAULT_PROBE_ATTEMPTS,
    retry_delay_s: float = _DEFAULT_RETRY_DELAY_S,
) -> tuple[bool, str | None]:
    """Try a minimal OANDA:XAUUSD fetch with retries; return (ok, failure_detail)."""
    attempts = max(1, int(max_attempts))
    last_detail: str | None = None

    for attempt in range(1, attempts + 1):
        ok, detail, retryable = _probe_once(timeout_s=timeout_s)
        if ok:
            if attempt > 1:
                logger.info(
                    "TradingView connectivity probe succeeded on attempt %d/%d",
                    attempt,
                    attempts,
                )
            else:
                logger.info("TradingView connectivity probe succeeded")
            return True, None

        last_detail = detail
        if not retryable or attempt >= attempts:
            break

        logger.info(
            "TradingView connectivity probe attempt %d/%d failed (%s); retrying in %.1fs",
            attempt,
            attempts,
            detail or "unknown",
            retry_delay_s,
        )
        time.sleep(max(0.0, retry_delay_s))

    if attempts > 1 and last_detail:
        last_detail = f"{last_detail}（已自动重试 {attempts} 次）"
    return False, last_detail
