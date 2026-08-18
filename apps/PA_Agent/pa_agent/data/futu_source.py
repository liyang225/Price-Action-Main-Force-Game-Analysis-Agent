"""Futu OpenD-backed market-data source."""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from threading import RLock, Timer
from typing import Any
from zoneinfo import ZoneInfo

from pa_agent.config.settings import Settings
from pa_agent.data.base import DataSource, DataSourceTransientError, KlineBar, normalize_kline_bar

logger = logging.getLogger(__name__)

_SUPPORTED_TIMEFRAMES: tuple[str, ...] = (
    "1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "3h", "4h", "1d", "1w", "1M",
)
_KLTYPE_BY_TIMEFRAME: dict[str, str] = {
    "1m": "K_1M", "3m": "K_3M", "5m": "K_5M", "10m": "K_10M", "15m": "K_15M",
    "30m": "K_30M", "1h": "K_60M", "2h": "K_120M", "3h": "K_180M", "4h": "K_240M",
    "1d": "K_DAY", "1w": "K_WEEK", "1M": "K_MON",
}
# Futu uses the same names for the subscription and query K-line enums.
_SUBTYPE_BY_TIMEFRAME: dict[str, str] = dict(_KLTYPE_BY_TIMEFRAME)
_MIN_OPEND_SUBSCRIPTION_SECONDS = 60.1
_FUTU_CODE_RE = re.compile(r"^(HK|US|SH|SZ|BJ)\.([A-Z0-9._-]+)$", re.IGNORECASE)
_A_SHARE_SUFFIX_RE = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$", re.IGNORECASE)
_CN_TZ = ZoneInfo("Asia/Shanghai")
_US_TZ = ZoneInfo("America/New_York")
_PRESET_SYMBOLS: tuple[str, ...] = ("SH.600519", "SZ.000001", "HK.00700", "US.AAPL")


def normalize_futu_symbol(symbol: str, exchange: str | None = None) -> str:
    """Normalize common stock-code inputs to Futu's ``MARKET.CODE`` format."""
    raw = (symbol or "").strip().upper().replace(":", ".")
    match = _FUTU_CODE_RE.fullmatch(raw)
    if match:
        market, code = match.groups()
        return f"HK.{code.zfill(5)}" if market == "HK" and code.isdigit() else f"{market}.{code}"
    match = _A_SHARE_SUFFIX_RE.fullmatch(raw)
    if match:
        code, exchange = match.groups()
        return f"{exchange}.{code}"
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 6:
        return raw
    if digits.startswith("920"):
        return f"BJ.{digits}"
    if digits.startswith(("00", "30", "159", "16", "180")):
        return f"SZ.{digits}"
    if digits.startswith(("60", "688", "51", "58", "501", "502", "508")):
        return f"SH.{digits}"
    exchange_prefix = {
        "SSE": "SH",
        "SH": "SH",
        "SHSE": "SH",
        "SZSE": "SZ",
        "SZ": "SZ",
        "XSHE": "SZ",
    }.get(str(exchange or "").strip().upper())
    if exchange_prefix:
        return f"{exchange_prefix}.{digits}"
    if digits.startswith(("689", "900")):
        return f"SH.{digits}"
    if digits.startswith(("150", "161", "162", "163", "164", "165", "166", "167", "168", "169", "184", "200", "399")):
        return f"SZ.{digits}"
    if digits.startswith(("4", "8")):
        return f"BJ.{digits}"
    return f"SH.{digits}"


def _number(row: Any, key: str, default: float = 0.0) -> float:
    try:
        value = row[key]
        return default if value is None or value != value else float(value)
    except (KeyError, TypeError, ValueError):
        return default


def _time_key_to_ts_ms(value: object, market: str) -> int:
    text = str(value).strip()
    tz = _US_TZ if market == "US" else _CN_TZ
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=tz).timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(f"Invalid Futu time_key: {value!r}")


def _df_to_bars_newest_first(
    df: Any, n: int, market: str, timeframe: str
) -> list[KlineBar]:
    if df is None or getattr(df, "empty", True):
        return []
    required = {"time_key", "open", "high", "low", "close"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Futu data missing fields: {', '.join(missing)}")
    ordered = df.sort_values("time_key", ascending=False).reset_index(drop=True)
    bars: list[KlineBar] = []
    for index, row in ordered.head(n).iterrows():
        timestamp = _time_key_to_ts_ms(row["time_key"], market)
        bars.append(normalize_kline_bar(KlineBar(
            seq=index + 1,
            ts_open=float(timestamp),
            open=_number(row, "open"), high=_number(row, "high"), low=_number(row, "low"),
            close=_number(row, "close"), volume=_number(row, "volume"),
            amount=_number(row, "turnover"),
            # Keep Futu's close label for the chart.  Waiting helpers use the
            # explicit flag instead of adding one more K-line interval.
            closed=index != 0 or timestamp <= int(time.time() * 1000),
            timestamp_is_close=True,
        )))
    return bars


class FutuSource(DataSource):
    """K-line source using the local Futu OpenD quote gateway."""

    kline_timestamp_is_close = True

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings
        self._context: Any | None = None
        self._symbol = ""
        self._timeframe = ""
        self._exchange = ""
        self._connected = False
        self._latest_quote_summary: dict[str, float] | None = None
        self._subscribed_key: tuple[str, str] | None = None
        self._subscription_started_at: dict[tuple[str, str], float] = {}
        self._pending_unsubscribes: dict[tuple[str, str], Timer] = {}
        self._subscription_lock = RLock()

    def connect(self) -> None:
        try:
            from futu import OpenQuoteContext
        except ImportError as exc:
            raise DataSourceTransientError(
                "未安装 futu-api。请使用项目环境执行: pip install futu-api"
            ) from exc
        host, port = self._opend_address()
        try:
            self._context = OpenQuoteContext(host=host, port=port)
        except Exception as exc:
            raise DataSourceTransientError(
                f"无法连接富途 OpenD ({host}:{port})：{exc}。请启动并登录 OpenD。"
            ) from exc
        self._connected = True
        logger.info("FutuSource connected to OpenD %s:%s", host, port)

    def disconnect(self) -> None:
        # Closing an OpenD context releases its subscriptions immediately.  This
        # avoids Futu's one-minute minimum subscription interval on shutdown.
        with self._subscription_lock:
            for timer in self._pending_unsubscribes.values():
                timer.cancel()
            self._pending_unsubscribes.clear()
            self._subscription_started_at.clear()
            self._subscribed_key = None
        context, self._context = self._context, None
        self._connected = False
        if context is not None:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                logger.debug("Futu OpenD context close failed", exc_info=True)
        logger.info("FutuSource disconnected")

    def list_symbols(self) -> list[str]:
        return list(_PRESET_SYMBOLS)

    def supported_timeframes(self) -> list[str]:
        return list(_SUPPORTED_TIMEFRAMES)

    def set_exchange(self, exchange: str | None) -> None:
        """Use an explicitly selected SSE/SZSE venue for bare A-share codes."""
        self._exchange = str(exchange or "").strip().upper()

    def subscribe(self, symbol: str, timeframe: str) -> None:
        if timeframe not in _KLTYPE_BY_TIMEFRAME:
            raise ValueError(f"Futu 当前支持: {' / '.join(_SUPPORTED_TIMEFRAMES)}")
        code = normalize_futu_symbol(symbol, exchange=self._exchange)
        if not _FUTU_CODE_RE.fullmatch(code):
            raise ValueError("富途代码无效，请输入如 SH.600519、HK.00700 或 US.AAPL")
        with self._subscription_lock:
            if self._subscribed_key is not None and self._subscribed_key != (code, timeframe):
                self._cancel_opend_subscription()
        self._symbol = code
        self._timeframe = timeframe
        if self._connected and self._context is not None:
            self._ensure_opend_subscription()
        logger.info("FutuSource subscribed: %s %s", code, timeframe)

    def unsubscribe(self) -> None:
        with self._subscription_lock:
            self._cancel_opend_subscription()
        self._symbol = ""
        self._timeframe = ""
        logger.info("FutuSource unsubscribed")

    def is_symbol_available(self, symbol: str) -> bool:
        return bool(_FUTU_CODE_RE.fullmatch(normalize_futu_symbol(symbol, self._exchange)))

    def latest_market_summary(self) -> dict[str, float] | None:
        """Return the most recent OpenD quote snapshot for chart chrome."""
        return dict(self._latest_quote_summary) if self._latest_quote_summary else None

    def get_sector_constituents(self, sector_code: str) -> tuple[str, ...]:
        """Ask OpenD whether a plate code resolves to actual constituents."""
        if not self._connected or self._context is None:
            raise DataSourceTransientError("Futu 未连接，请先启动并登录 OpenD")
        code = str(sector_code or "").strip()
        if not code:
            raise ValueError("富途板块代码不能为空")
        try:
            from futu import RET_OK

            ret, data = self._context.get_plate_stock(code)
        except Exception as exc:
            raise DataSourceTransientError(
                f"Futu OpenD 板块查询失败 {code}: {exc}"
            ) from exc
        if ret != RET_OK:
            raise DataSourceTransientError(f"Futu OpenD 板块查询错误 {code}: {data}")
        records = (
            data.to_dict("records")
            if hasattr(data, "to_dict")
            else list(data or ())
        )
        constituents = tuple(
            dict.fromkeys(
                str(row.get("code") or "").strip()
                for row in records
                if isinstance(row, dict) and str(row.get("code") or "").strip()
            )
        )
        if not constituents:
            raise DataSourceTransientError(
                f"Futu OpenD 没有返回板块成分股: {code}"
            )
        return constituents

    def latest_snapshot(self, n: int) -> list[KlineBar]:
        if not self._connected or self._context is None:
            raise DataSourceTransientError("Futu 未连接，请先启动并登录 OpenD")
        if not self._symbol or not self._timeframe:
            raise DataSourceTransientError("Futu 未订阅品种/周期")
        try:
            from futu import AuType, KLType, RET_OK
            self._ensure_opend_subscription()
            ktype = getattr(KLType, _KLTYPE_BY_TIMEFRAME[self._timeframe])
            ret, data = self._context.get_cur_kline(
                self._symbol, min(max(n + 60, 120), 1000), ktype=ktype, autype=AuType.QFQ
            )
        except Exception as exc:
            raise DataSourceTransientError(f"Futu OpenD K线调用失败: {exc}") from exc
        if ret != RET_OK:
            raise DataSourceTransientError(f"Futu OpenD 返回错误: {data}")
        try:
            bars = _df_to_bars_newest_first(
                data, n, self._symbol.split(".", 1)[0], self._timeframe
            )
        except Exception as exc:
            raise DataSourceTransientError(f"Futu K线数据解析失败: {exc}") from exc
        self._refresh_market_summary()
        if not bars:
            raise DataSourceTransientError(f"Futu 未返回数据: {self._symbol} {self._timeframe}")
        return bars

    def search_news(self, keyword: str) -> list[dict[str, object]]:
        """Return OpenD's embedded news summaries without opening article URLs."""
        if not self._connected or self._context is None:
            raise DataSourceTransientError("Futu is not connected; news search unavailable")
        try:
            from futu import RET_OK

            ret, data = self._context.get_search_news(
                keyword=str(keyword).strip(), max_count=10
            )
        except Exception as exc:
            raise DataSourceTransientError(f"Futu OpenD news search failed: {exc}") from exc
        if ret != RET_OK:
            raise DataSourceTransientError(f"Futu OpenD news search error: {data}")
        if data is None:
            return []
        records = data.to_dict("records") if hasattr(data, "to_dict") else list(data)
        result: list[dict[str, object]] = []
        for row in records:
            if not isinstance(row, dict):
                continue
            result.append(
                {
                    "title": row.get("title", ""),
                    "summary": row.get("summary", row.get("content", "")),
                    "url": row.get("url", ""),
                    "publish_time": row.get("publish_time", ""),
                    "source": row.get("source", "Futu OpenD"),
                    "related_securities": row.get("related_securities", ()),
                }
            )
        return result

    def _refresh_market_summary(self) -> None:
        """Fetch optional quote fields without making K-line retrieval fail."""
        if self._context is None or not self._symbol:
            return
        getter = getattr(self._context, "get_market_snapshot", None)
        if not callable(getter):
            return
        try:
            from futu import RET_OK

            ret, data = getter([self._symbol])
            if ret != RET_OK or data is None or getattr(data, "empty", True):
                return
            row = data.iloc[0]
            values: dict[str, float] = {}
            for output_key, source_key in (
                ("last_price", "last_price"),
                ("open_price", "open_price"),
                ("high_price", "high_price"),
                ("low_price", "low_price"),
                ("volume", "volume"),
                ("turnover", "turnover"),
                ("turnover_rate", "turnover_rate"),
                ("change_rate", "change_rate"),
            ):
                value = _number(row, source_key, float("nan"))
                if value == value:
                    values[output_key] = value
            # Current Futu snapshots expose prev_close_price but not a regular
            # session change_rate.  Derive it so the chart header always has a
            # normal-session percentage change when a prior close is present.
            previous_close = _number(row, "prev_close_price", float("nan"))
            last_price = values.get("last_price")
            if (
                "change_rate" not in values
                and last_price is not None
                and previous_close == previous_close
                and previous_close != 0
            ):
                values["change_rate"] = (last_price - previous_close) / previous_close * 100
            if values:
                self._latest_quote_summary = values
        except Exception:  # noqa: BLE001
            logger.debug("Futu market snapshot unavailable", exc_info=True)

    def _ensure_opend_subscription(self) -> None:
        """Subscribe the active quote only when OpenD has not seen it yet."""
        key = (self._symbol, self._timeframe)
        with self._subscription_lock:
            if self._subscribed_key == key:
                return
            pending = self._pending_unsubscribes.pop(key, None)
            if pending is not None:
                pending.cancel()
                self._subscribed_key = key
                return
            if not self._symbol or not self._timeframe or self._context is None:
                raise DataSourceTransientError("Futu 未连接或未订阅品种/周期")
            try:
                from futu import RET_OK, SubType

                subtype = getattr(SubType, _SUBTYPE_BY_TIMEFRAME[self._timeframe])
                ret, message = self._context.subscribe(
                    [self._symbol], [subtype], subscribe_push=False
                )
            except Exception as exc:
                raise DataSourceTransientError(f"Futu OpenD K线订阅调用失败: {exc}") from exc
            if ret != RET_OK:
                raise DataSourceTransientError(f"Futu K线订阅失败: {message}")
            self._subscribed_key = key
            self._subscription_started_at[key] = time.monotonic()

    def _cancel_opend_subscription(self) -> None:
        """Release the active subscription after Futu's one-minute minimum."""
        key = self._subscribed_key
        self._subscribed_key = None
        if key is None or self._context is None:
            return
        delay = max(
            0.0,
            _MIN_OPEND_SUBSCRIPTION_SECONDS
            - (time.monotonic() - self._subscription_started_at.get(key, time.monotonic())),
        )
        if delay > 0:
            timer = Timer(delay, self._unsubscribe_after_minimum_interval, args=(key, self._context))
            timer.daemon = True
            self._pending_unsubscribes[key] = timer
            timer.start()
            logger.info("Futu K线退订延后 %.1f 秒: %s %s", delay, key[0], key[1])
            return
        self._unsubscribe_now(key, self._context)

    def _unsubscribe_after_minimum_interval(self, key: tuple[str, str], context: Any) -> None:
        with self._subscription_lock:
            timer = self._pending_unsubscribes.pop(key, None)
            if timer is None or self._subscribed_key == key or context is not self._context:
                return
            self._unsubscribe_now(key, context)

    def _unsubscribe_now(self, key: tuple[str, str], context: Any) -> None:
        symbol, timeframe = key
        self._subscription_started_at.pop(key, None)
        try:
            from futu import RET_OK, SubType

            subtype = getattr(SubType, _SUBTYPE_BY_TIMEFRAME[timeframe])
            ret, message = context.unsubscribe([symbol], [subtype])
            if ret != RET_OK:
                logger.warning("Futu K线退订失败: %s", message)
        except Exception:  # noqa: BLE001
            logger.warning("Futu OpenD K线退订调用失败", exc_info=True)

    def _opend_address(self) -> tuple[str, int]:
        settings = getattr(self._settings, "futu", None)
        host = str(getattr(settings, "opend_host", "") or os.environ.get("FUTU_OPEND_HOST") or "127.0.0.1").strip()
        raw_port = getattr(settings, "opend_port", None) or os.environ.get("FUTU_OPEND_PORT") or 11111
        try:
            port = int(raw_port)
        except (TypeError, ValueError) as exc:
            raise DataSourceTransientError(f"FUTU_OPEND_PORT 无效: {raw_port!r}") from exc
        if not host or not 1 <= port <= 65535:
            raise DataSourceTransientError(f"富途 OpenD 地址无效: {host}:{port}")
        return host, port
