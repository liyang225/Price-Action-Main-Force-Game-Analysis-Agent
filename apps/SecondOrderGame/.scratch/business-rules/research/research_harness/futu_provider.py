from __future__ import annotations

import importlib
import time
from datetime import date
from typing import Any, Callable, Iterable, Mapping

from .errors import HistoryProviderError
from .models import HistoryRequest


class FutuHistoryProvider:
    """Futu OpenD history adapter with explicit period mapping and pagination."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        *,
        quote_context: Any | None = None,
        futu_api: Any | None = None,
        max_count: int = 1000,
        page_delay_seconds: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_count < 1 or max_count > 1000:
            raise ValueError("max_count must be between 1 and 1000")
        if page_delay_seconds < 0:
            raise ValueError("page_delay_seconds must not be negative")
        self.host = host
        self.port = port
        self.max_count = max_count
        self.page_delay_seconds = page_delay_seconds
        self._sleep = sleep
        self._futu = futu_api
        self._context = quote_context
        self._owns_context = quote_context is None
        self.last_page_count: int | None = None

    def _load_futu(self):
        if self._futu is None:
            try:
                self._futu = importlib.import_module("futu")
            except ImportError as exc:
                raise HistoryProviderError(
                    "futu package is not installed; install futu-api or use an offline provider"
                ) from exc
        return self._futu

    def _load_context(self):
        if self._context is None:
            futu = self._load_futu()
            try:
                self._context = futu.OpenQuoteContext(host=self.host, port=self.port)
            except Exception as exc:
                raise HistoryProviderError(
                    f"cannot connect to Futu OpenD at {self.host}:{self.port}: {exc}"
                ) from exc
        return self._context

    def fetch_history(self, request: HistoryRequest) -> Iterable[Mapping[str, Any]]:
        self.last_page_count = None
        futu = self._load_futu()
        context = self._load_context()
        try:
            ktype = self._period_constant(futu, request.period)
            fields = [getattr(futu.KL_FIELD, "ALL", "ALL")]
            autype = self._autype_constant(futu, request.kind)
        except AttributeError as exc:
            raise HistoryProviderError(f"futu API is missing a required enum: {exc}") from exc

        page_key = None
        rows: list[dict[str, Any]] = []
        seen_page_keys: set[Any] = set()
        page_count = 0
        while True:
            if page_key in seen_page_keys:
                self.last_page_count = page_count
                raise HistoryProviderError(
                    f"Futu returned a repeated pagination key for {request.code}: {page_key!r}"
                )
            if page_key is not None:
                seen_page_keys.add(page_key)
            try:
                result = context.request_history_kline(
                    code=request.code,
                    start=request.start.isoformat(),
                    end=request.end.isoformat(),
                    ktype=ktype,
                    autype=autype,
                    fields=fields,
                    max_count=self.max_count,
                    page_req_key=page_key,
                )
            except Exception as exc:
                self.last_page_count = page_count
                raise HistoryProviderError(
                    f"Futu request failed for {request.code} ({request.period}): {exc}"
                ) from exc
            if not isinstance(result, tuple) or len(result) != 3:
                self.last_page_count = page_count
                raise HistoryProviderError(
                    f"unexpected Futu response for {request.code}: expected (ret, data, page_key)"
                )
            ret_code, data, next_page_key = result
            if ret_code != getattr(futu, "RET_OK", 0):
                self.last_page_count = page_count
                raise HistoryProviderError(
                    f"Futu request failed for {request.code} ({request.period}): {data}"
                )
            try:
                rows.extend(_frame_records(data, request.code))
            except Exception:
                self.last_page_count = page_count + 1
                raise
            page_count += 1
            if next_page_key is None:
                break
            page_key = next_page_key
            if self.page_delay_seconds:
                self._sleep(self.page_delay_seconds)

        self.last_page_count = page_count
        return tuple(rows)

    @staticmethod
    def _period_constant(futu: Any, period: str) -> Any:
        name = {"day": "K_DAY", "120m": "K_120M"}[period]
        return getattr(futu.KLType, name)

    @staticmethod
    def _autype_constant(futu: Any, kind: str) -> Any:
        au_type = getattr(futu, "AuType")
        if kind == "sector_index":
            return getattr(au_type, "NONE", getattr(au_type, "QFQ"))
        return getattr(au_type, "QFQ")

    def close(self) -> None:
        if self._context is not None and self._owns_context:
            close = getattr(self._context, "close", None)
            if callable(close):
                close()
            self._context = None

    def __enter__(self) -> "FutuHistoryProvider":
        self._load_context()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _frame_records(data: Any, requested_code: str) -> list[dict[str, Any]]:
    if data is None:
        return []
    if hasattr(data, "to_dict"):
        try:
            records = data.to_dict(orient="records")
        except TypeError:
            records = data.to_dict("records")
    elif isinstance(data, list):
        records = data
    else:
        raise HistoryProviderError(
            f"unexpected Futu data payload for {requested_code}: {type(data).__name__}"
        )
    output: list[dict[str, Any]] = []
    for row in records:
        if not isinstance(row, Mapping):
            raise HistoryProviderError(f"unexpected Futu row for {requested_code}: {row!r}")
        converted = {str(key): _scalar(value) for key, value in row.items()}
        converted.setdefault("code", requested_code)
        time_value = converted.get("time_key", converted.get("timestamp"))
        if time_value is not None and "trading_date" not in converted:
            converted["trading_date"] = _date_from_value(time_value)
        output.append(converted)
    return output


def _scalar(value: Any) -> Any:
    if hasattr(value, "item") and callable(value.item):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def _date_from_value(value: Any) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime().date()
    if hasattr(value, "date"):
        return value.date()
    raise HistoryProviderError(f"cannot parse Futu time_key {value!r}")
