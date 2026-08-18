"""MetaTrader 5 data source.

Requires MetaTrader 5 terminal to be installed and running on Windows.
Install the Python package: pip install MetaTrader5

Usage:
    source = MT5Source()
    source.connect()                        # connects to running MT5 terminal
    source.subscribe("XAUUSD", "1h")
    bars = source.latest_snapshot(200)      # newest-first, bars[0] = forming bar
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pa_agent.data.base import (
    DataSource,
    DataSourceTransientError,
    KlineBar,
    normalize_kline_bar,
)

logger = logging.getLogger(__name__)

# Map our timeframe strings → MT5 TIMEFRAME constants (by name)
_TF_MAP: dict[str, str] = {
    "1m":  "TIMEFRAME_M1",
    "2m":  "TIMEFRAME_M2",
    "3m":  "TIMEFRAME_M3",
    "5m":  "TIMEFRAME_M5",
    "10m": "TIMEFRAME_M10",
    "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30",
    "1h":  "TIMEFRAME_H1",
    "2h":  "TIMEFRAME_H2",
    "3h":  "TIMEFRAME_H3",
    "4h":  "TIMEFRAME_H4",
    "6h":  "TIMEFRAME_H6",
    "8h":  "TIMEFRAME_H8",
    "12h": "TIMEFRAME_H12",
    "1d":  "TIMEFRAME_D1",
    "1w":  "TIMEFRAME_W1",
    "1M":  "TIMEFRAME_MN1",
}


class MT5Source(DataSource):
    """Live K-line data from MetaTrader 5 terminal.

    Zero latency — data comes directly from your broker via the MT5 terminal.
    MT5 terminal must be open and logged in before calling connect().
    """

    def __init__(self) -> None:
        self._symbol: str = ""
        self._timeframe: str = ""
        self._connected: bool = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Connect to the running MT5 terminal."""
        try:
            import MetaTrader5 as mt5  # type: ignore[import]
        except ImportError as exc:
            raise DataSourceTransientError(
                "MetaTrader5 package not installed — run: pip install MetaTrader5"
            ) from exc

        if not mt5.initialize():
            error = mt5.last_error()
            raise DataSourceTransientError(
                f"MT5 initialize() failed: {error}. "
                "Make sure MetaTrader 5 terminal is open and logged in."
            )

        info = mt5.terminal_info()
        if info is not None:
            logger.info(
                "MT5 connected: terminal=%s, build=%s, connected=%s",
                info.name, info.build, info.connected,
            )
        else:
            logger.info("MT5 connected (terminal info unavailable)")

        self._connected = True

    def disconnect(self) -> None:
        """Shut down the MT5 connection."""
        if self._connected:
            try:
                import MetaTrader5 as mt5  # type: ignore[import]
                mt5.shutdown()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MT5 shutdown error: %s", exc)
        self._connected = False
        logger.info("MT5Source disconnected")

    # ── Discovery ─────────────────────────────────────────────────────────────

    def is_symbol_available(self, symbol: str) -> bool:
        """Return True if *symbol* exists in the connected MT5 terminal."""
        name = (symbol or "").strip()
        if not name:
            return False
        if not self._connected:
            return True
        try:
            import MetaTrader5 as mt5  # type: ignore[import]
            return mt5.symbol_info(name) is not None
        except Exception as exc:  # noqa: BLE001
            logger.debug("MT5 symbol_info(%s) failed: %s", name, exc)
            return False

    def list_symbols(self) -> list[str]:
        """Return all symbols available in the MT5 terminal."""
        if not self._connected:
            return ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
                    "AUDUSD", "USDCAD", "NZDUSD", "XAGUSD"]
        try:
            import MetaTrader5 as mt5  # type: ignore[import]
            symbols = mt5.symbols_get()
            if symbols:
                return [s.name for s in symbols]
        except Exception as exc:  # noqa: BLE001
            logger.warning("MT5 list_symbols failed: %s", exc)
        return []

    def supported_timeframes(self) -> list[str]:
        return list(_TF_MAP.keys())

    # ── Subscription ──────────────────────────────────────────────────────────

    def subscribe(self, symbol: str, timeframe: str) -> None:
        if timeframe not in _TF_MAP:
            raise ValueError(
                f"Unsupported timeframe: {timeframe!r}. "
                f"Use one of {list(_TF_MAP)}"
            )
        self._symbol = symbol
        self._timeframe = timeframe
        # Tell MT5 to subscribe to this symbol's real-time data
        if self._connected:
            try:
                import MetaTrader5 as mt5  # type: ignore[import]
                mt5.symbol_select(symbol, True)
            except Exception:  # noqa: BLE001
                pass
        logger.info("MT5Source subscribed: %s %s", symbol, timeframe)

    def unsubscribe(self) -> None:
        self._symbol = ""
        self._timeframe = ""
        logger.info("MT5Source unsubscribed")

    def server_time_ms(self, symbol: str | None = None) -> int | None:
        """Broker/server time from the latest MT5 tick (milliseconds since epoch).

        Use this for forming-bar countdowns so ``now`` matches ``rate['time']``
        on K-line bars. Falls back to None when disconnected or tick unavailable.
        """
        if not self._connected:
            return None
        name = (symbol or self._symbol or "").strip()
        if not name:
            return None
        try:
            import MetaTrader5 as mt5  # type: ignore[import]

            tick = mt5.symbol_info_tick(name)
            if tick is None:
                return None
            time_msc = getattr(tick, "time_msc", None)
            if time_msc:
                return int(time_msc)
            tick_time = getattr(tick, "time", None)
            if tick_time:
                return int(tick_time) * 1000
        except Exception as exc:  # noqa: BLE001
            logger.debug("MT5 server_time_ms(%s) failed: %s", name, exc)
        return None

    # ── Data fetch ────────────────────────────────────────────────────────────

    def latest_snapshot(self, n: int) -> list[KlineBar]:
        """Return *n* bars newest-first; bars[0] is the forming (unclosed) bar.

        Uses copy_rates_from_pos(symbol, timeframe, 0, n+1):
        - position 0 = current forming bar
        - position 1..n = closed bars
        """
        if not self._connected:
            raise DataSourceTransientError("Not connected — call connect() first")
        if not self._symbol or not self._timeframe:
            raise DataSourceTransientError("Not subscribed — call subscribe() first")

        try:
            import MetaTrader5 as mt5  # type: ignore[import]
        except ImportError as exc:
            raise DataSourceTransientError("MetaTrader5 not installed") from exc

        tf_name = _TF_MAP[self._timeframe]
        try:
            tf_const = getattr(mt5, tf_name)
        except AttributeError as exc:
            raise DataSourceTransientError(
                f"MT5 timeframe constant {tf_name!r} not found"
            ) from exc

        # Ensure the symbol is selected/subscribed in MT5 for real-time data
        try:
            mt5.symbol_select(self._symbol, True)
        except Exception:  # noqa: BLE001
            pass  # Non-fatal; proceed with fetch

        # Fetch n+1 bars starting from position 0 (current forming bar)
        rates = mt5.copy_rates_from_pos(self._symbol, tf_const, 0, n + 1)

        if rates is None or len(rates) == 0:
            error = mt5.last_error()
            raise DataSourceTransientError(
                f"MT5 copy_rates_from_pos failed for {self._symbol} {self._timeframe}: "
                f"{error}"
            )

        # copy_rates_from_pos returns oldest-first (ascending time order).
        # rates[0] is the OLDEST bar, rates[-1] is the NEWEST (forming) bar.
        # We need newest-first, so reverse the array before building KlineBar list.
        bars: list[KlineBar] = []
        for i, rate in enumerate(reversed(rates)):
            # rate fields: time, open, high, low, close, tick_volume, spread, real_volume
            ts_ms = int(rate["time"]) * 1000  # MT5 gives UTC seconds
            try:
                vol = float(rate["tick_volume"])
            except (ValueError, KeyError):
                try:
                    vol = float(rate["real_volume"])
                except (ValueError, KeyError):
                    vol = 0.0

            if i == 0:
                # Position 0 is the newest (potentially forming) bar in MT5.
                # Mark it as closed=False so downstream is_bar_still_forming()
                # can do a proper wall-clock + safety-net check.
                # (is_bar_still_forming has a 6 h safety margin for daily/weekly
                # bars to handle stale broker server time during weekends.)
                is_forming = True
            else:
                is_forming = False

            bars.append(
                normalize_kline_bar(
                    KlineBar(
                        seq=i + 1,
                        ts_open=ts_ms,
                        open=float(rate["open"]),
                        high=float(rate["high"]),
                        low=float(rate["low"]),
                        close=float(rate["close"]),
                        volume=vol,
                        closed=not is_forming,
                    )
                )
            )
            if len(bars) >= n:
                break

        return bars
