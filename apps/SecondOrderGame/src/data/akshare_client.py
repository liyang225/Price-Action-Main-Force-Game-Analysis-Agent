"""AkShare adapter for the provider-neutral market-data boundary.

AkShare returns pandas frames whose column names and numeric formatting have
changed across releases.  This module keeps that variability at the edge and
only exposes immutable :class:`DragonTiger` records to the rest of the app.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import date as calendar_date
import math
import re
import time
from threading import Event, Thread
from typing import Any

from .models import Bar, CapitalFlow, DragonTiger, LimitPoolRecord, NewsItem
from .protocol import DataSourceError


class AkShareApiError(DataSourceError):
    """AkShare failed or returned a payload we cannot safely normalize."""


class AkShareMarketDataSource:
    """Normalize AkShare's Eastmoney 龙虎榜 detail endpoint.

    ``akshare_module`` is injectable so tests never need a network or a
    pandas installation.  The call is made on a daemon worker and bounded by
    ``timeout_seconds``; a timed-out provider is reported as an error rather
    than being mistaken for a valid empty result.
    """

    endpoint = "stock_lhb_detail_em"

    def __init__(
        self,
        *,
        akshare_module: Any | None = None,
        timeout_seconds: float = 30.0,
        endpoint: str | None = None,
        institution_endpoint: str = "stock_lhb_jgmmtj_em",
        seat_endpoint: str = "stock_lhb_stock_detail_em",
    ) -> None:
        if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
            raise ValueError("timeout_seconds must be a finite positive number")
        self._akshare_module = akshare_module
        self._timeout_seconds = float(timeout_seconds)
        self._endpoint = endpoint or self.endpoint
        self._institution_endpoint = institution_endpoint
        self._seat_endpoint = seat_endpoint

    def get_dragon_tiger(self, code: str, date: str) -> DragonTiger | None:
        _validate_request(code, date)
        records = self.get_dragon_tiger_records(code, date)
        if not records:
            return None
        if len(records) == 1:
            return records[0]
        return _merge_records(records, code=code, date=date, endpoint=self._endpoint)

    def get_kline(self, code: str, ktype: str, start: str, end: str) -> list[Bar]:
        raise DataSourceError("AkShare adapter only provides 龙虎榜 data")

    def get_capital_flow(self, code: str, date: str) -> CapitalFlow | None:
        raise DataSourceError("AkShare adapter only provides 龙虎榜 data")

    def get_capital_flow_range(
        self, code: str, start: str, end: str
    ) -> list[CapitalFlow]:
        raise DataSourceError("AkShare adapter only provides 龙虎榜 data")

    def get_sector_capital_flow_history(
        self, sector_name: str
    ) -> tuple[CapitalFlow, ...]:
        """Read recent industry or concept flow history by its displayed name.

        AkShare's board endpoints use Eastmoney's Chinese board names rather
        than Futu plate codes.  Amounts are converted from yuan to the ledger's
        ten-thousand-yuan unit at this boundary.
        """
        name = str(sector_name or "").strip()
        if not name:
            raise ValueError("sector_name must not be empty")
        module = self._module()
        errors: list[Exception] = []
        available = False
        for endpoint in (
            "stock_sector_fund_flow_hist",
            "stock_concept_fund_flow_hist",
        ):
            method = getattr(module, endpoint, None)
            if not callable(method):
                continue
            available = True
            try:
                rows = _rows(
                    _call_with_timeout(
                        method,
                        {"symbol": name},
                        timeout_seconds=self._timeout_seconds,
                    )
                )
            except Exception as exc:  # noqa: BLE001 -- try the other board class
                errors.append(exc)
                continue
            if not rows:
                continue
            try:
                return tuple(_normalize_sector_flow_row(row, code=name) for row in rows)
            except (TypeError, ValueError) as exc:
                raise AkShareApiError(
                    f"AkShare board capital-flow row normalization failed: {exc}"
                ) from exc
        if errors:
            raise AkShareApiError(
                f"AkShare board capital-flow request failed: {errors[-1]}"
            ) from errors[-1]
        if not available:
            raise AkShareApiError("AkShare board capital-flow endpoints are unavailable")
        return ()

    def search_news(self, keyword: str) -> list[NewsItem]:
        raise DataSourceError("AkShare adapter only provides 龙虎榜 data")

    def get_sector_constituents(self, sector_code: str) -> tuple[str, ...]:
        raise DataSourceError("sector membership must come from Futu plate codes")

    def get_market_snapshots(self, codes: Iterable[str]) -> tuple[object, ...]:
        raise DataSourceError("market snapshots must come from the Futu market source")

    def get_limit_pool(self, date: str) -> tuple[LimitPoolRecord, ...]:
        """Normalize Eastmoney rise/fall pools without using industry strings."""
        _validate_pool_date(date)
        module = self._module()
        compact = date.replace("-", "")
        rise_method = getattr(module, "stock_zt_pool_em", None)
        fall_method = getattr(module, "stock_zt_pool_dtgc_em", None)
        if not callable(rise_method) or not callable(fall_method):
            raise AkShareApiError("AkShare limit-pool endpoints are unavailable")
        try:
            rise_rows = _rows(_call_with_timeout(
                rise_method, {"date": compact}, timeout_seconds=self._timeout_seconds
            ))
            fall_rows = _rows(_call_with_timeout(
                fall_method, {"date": compact}, timeout_seconds=self._timeout_seconds
            ))
        except Exception as exc:
            if isinstance(exc, AkShareApiError):
                raise
            raise AkShareApiError(f"AkShare limit-pool request failed: {exc}") from exc
        result = [
            LimitPoolRecord(date, _text(row, "代码", "code"), int(_number(row, "连板数") or 1), "rise")
            for row in rise_rows
        ]
        result.extend(
            LimitPoolRecord(date, _text(row, "代码", "code"), int(_number(row, "连续跌停") or 1), "fall")
            for row in fall_rows
        )
        if any(not item.code for item in result):
            raise AkShareApiError("AkShare limit-pool row is missing stock code")
        return tuple(result)

    def get_trading_days(self, market: str, start: str, end: str) -> tuple[calendar_date, ...]:
        raise DataSourceError("trading calendar must come from the Futu market source")

    def get_dragon_tiger_records(
        self, code: str, date: str
    ) -> tuple[DragonTiger, ...]:
        _validate_request(code, date)
        deadline = time.monotonic() + self._timeout_seconds
        module = self._module()
        method = getattr(module, self._endpoint, None)
        if not callable(method):
            raise AkShareApiError(
                f"AkShare module has no callable {self._endpoint} endpoint"
            )
        try:
            payload = _call_with_timeout(
                method,
                {"start_date": date.replace("-", ""), "end_date": date.replace("-", "")},
                timeout_seconds=_remaining(deadline),
            )
            rows = _rows(payload)
        except AkShareApiError:
            raise
        except Exception as exc:
            raise AkShareApiError(f"AkShare 龙虎榜 request failed: {exc}") from exc

        result: list[DragonTiger] = []
        for row in rows:
            row_code = _text(row, "代码", "股票代码", "证券代码", "code")
            row_date = _text(
                row, "日期", "交易日期", "上榜日", "上榜日期", "date", "trade_date"
            )
            if row_code and not _same_code(row_code, code):
                continue
            if row_date and not _same_date(row_date, date):
                continue
            try:
                result.append(
                    _normalize_row(
                        row,
                        code=code,
                        date=date,
                        endpoint=self._endpoint,
                    )
                )
            except (TypeError, ValueError) as exc:
                raise AkShareApiError(
                    f"AkShare 龙虎榜 row normalization failed: {exc}"
                ) from exc
        return self._enrich_records(module, _deduplicate_records(result), date, deadline)

    def get_dragon_tiger_day(self, date: str) -> tuple[DragonTiger, ...]:
        """One market-wide 龙虎榜 list for a trading date, without seat enrichment.

        The detail endpoint already pages the whole market for a date range;
        per-code enrichment (institution seats, per-seat evidence) is what
        makes a single ``get_dragon_tiger`` call expensive.  This method
        returns the cheap base records for every listed stock so callers can
        intersect with a sector's constituents before enriching only the few
        matching codes.
        """
        _validate_pool_date(date)
        module = self._module()
        method = getattr(module, self._endpoint, None)
        if not callable(method):
            raise AkShareApiError(
                f"AkShare module has no callable {self._endpoint} endpoint"
            )
        try:
            payload = _call_with_timeout(
                method,
                {"start_date": date.replace("-", ""), "end_date": date.replace("-", "")},
                timeout_seconds=self._timeout_seconds,
            )
            rows = _rows(payload)
        except AkShareApiError:
            raise
        except Exception as exc:
            if _is_empty_daily_list_error(exc):
                return ()
            raise AkShareApiError(f"AkShare 龙虎榜 daily-list request failed: {exc}") from exc

        result: list[DragonTiger] = []
        for row in rows:
            row_date = _text(
                row, "日期", "交易日期", "上榜日", "上榜日期", "date", "trade_date"
            )
            if row_date and not _same_date(row_date, date):
                continue
            code = _text(row, "代码", "股票代码", "证券代码", "code")
            if not code:
                continue
            try:
                result.append(_normalize_row(row, code=code, date=date, endpoint=self._endpoint))
            except (TypeError, ValueError):
                continue  # 单行脏数据不中断整份列表
        return tuple(_deduplicate_records(result))

    def _enrich_records(
        self,
        module: Any,
        records: list[DragonTiger],
        date: str,
        deadline: float,
    ) -> tuple[DragonTiger, ...]:
        if not records:
            return ()
        enriched = records
        institution_method = getattr(module, self._institution_endpoint, None)
        if callable(institution_method):
            try:
                payload = _call_with_timeout(
                    institution_method,
                    {
                        "start_date": date.replace("-", ""),
                        "end_date": date.replace("-", ""),
                    },
                    timeout_seconds=_remaining(deadline),
                )
                institution_rows = _rows(payload)
            except Exception as exc:
                raise AkShareApiError(
                    f"AkShare institution 龙虎榜 request failed: {exc}"
                ) from exc
            for index, record in enumerate(enriched):
                matching = next(
                    (
                        row
                        for row in institution_rows
                        if _same_code(
                            _text(row, "代码", "股票代码", "code"), record.code
                        )
                        and _same_date(
                            _text(row, "上榜日期", "上榜日", "日期", "date"), date
                        )
                    ),
                    None,
                )
                if matching is not None:
                    net = _number(
                        matching,
                        "机构买入净额",
                        "机构净买额",
                        "机构净买入额",
                    )
                    if net is not None:
                        buy, sell = _split_signed(net)
                        enriched[index] = replace(
                            record,
                            institution_net_buy=buy,
                            institution_net_sell=sell,
                            institution_seats=("机构专用",),
                        )

        seat_method = getattr(module, self._seat_endpoint, None)
        if callable(seat_method):
            for index, record in enumerate(enriched):
                buy_rows = self._seat_rows(seat_method, record.code, date, "买入", deadline)
                sell_rows = self._seat_rows(seat_method, record.code, date, "卖出", deadline)
                enriched[index] = _attach_seat_evidence(record, buy_rows, sell_rows)
        return tuple(enriched)

    def _seat_rows(
        self, method: Any, code: str, date: str, flag: str, deadline: float
    ) -> list[Mapping[str, Any]]:
        try:
            payload = _call_with_timeout(
                method,
                {"symbol": _bare_code(code), "date": date.replace("-", ""), "flag": flag},
                timeout_seconds=_remaining(deadline),
            )
            return _rows(payload)
        except Exception as exc:
            raise AkShareApiError(
                f"AkShare seat 龙虎榜 request failed ({flag}): {exc}"
            ) from exc

    def _module(self) -> Any:
        if self._akshare_module is None:
            try:
                import akshare  # type: ignore[import-not-found]
            except ImportError as exc:
                raise AkShareApiError("akshare is not installed") from exc
            self._akshare_module = akshare
        return self._akshare_module


AkShareClient = AkShareMarketDataSource


def _normalize_row(
    row: Mapping[str, Any], *, code: str, date: str, endpoint: str
) -> DragonTiger:
    net_value = _number(
        row,
        "龙虎榜净买额",
        "龙虎榜净买入额",
        "净买额",
        "净买入额",
        "net_buy_amount",
    )
    buy_value = _number(row, "龙虎榜买入额", "买入额", "buy_amount")
    sell_value = _number(row, "龙虎榜卖出额", "卖出额", "sell_amount")
    if net_value is None and buy_value is not None and sell_value is not None:
        net_value = buy_value - sell_value
    if net_value is None:
        raise ValueError("missing 龙虎榜 net buy amount")

    institution_net = _number(
        row,
        "机构净买额",
        "机构净买入额",
        "机构买入净额",
        "机构净额",
        "institution_net_buy",
    )
    if institution_net is None:
        institution_buy = _number(row, "机构买入额", "institution_buy_amount")
        institution_sell = _number(row, "机构卖出额", "institution_sell_amount")
        if institution_buy is not None and institution_sell is not None:
            institution_net = institution_buy - institution_sell

    hot_net = _number(
        row,
        "游资净买额",
        "游资净买入额",
        "游资净额",
        "hot_money_net_buy",
    )
    if hot_net is None:
        hot_buy = _number(row, "游资买入额", "hot_money_buy_amount")
        hot_sell = _number(row, "游资卖出额", "hot_money_sell_amount")
        if hot_buy is not None and hot_sell is not None:
            hot_net = hot_buy - hot_sell

    institution_buy, institution_sell = _split_signed(institution_net)
    hot_buy, hot_sell = _split_signed(hot_net)
    institution_seats = _seat_names(
        row, "机构席位", "机构专用", "institution_seats", "institution_seat"
    )
    hot_money_seats = _seat_names(
        row, "游资席位", "知名游资", "hot_money_seats", "hot_money_seat"
    )
    reason = _text(row, "上榜原因", "龙虎榜原因", "reason") or ""
    return DragonTiger(
        date=date,
        code=code,
        reason=reason,
        net_buy_amount=float(net_value),
        buy_amount=_as_float(buy_value),
        sell_amount=_as_float(sell_value),
        institution_net_buy=institution_buy,
        institution_net_sell=institution_sell,
        hot_money_net_buy=hot_buy,
        hot_money_net_sell=hot_sell,
        institution_seats=institution_seats,
        hot_money_seats=hot_money_seats,
        source="AkShare",
        source_reference=f"{endpoint}:{date}",
    )


def _normalize_sector_flow_row(row: Mapping[str, Any], *, code: str) -> CapitalFlow:
    raw_date = _text(row, "日期", "交易日期", "date", "trade_date")
    try:
        day = calendar_date.fromisoformat(raw_date[:10]).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("missing or invalid board capital-flow date") from exc

    def amount(*names: str) -> float:
        value = _number(row, *names)
        if value is None:
            raise ValueError(f"missing board capital-flow field {names[0]}")
        return value / 10_000.0

    return CapitalFlow(
        date=day,
        code=code,
        super_in_flow=amount("超大单净流入-净额", "超大单净流入", "super_in_flow"),
        big_in_flow=amount("大单净流入-净额", "大单净流入", "big_in_flow"),
        mid_in_flow=amount("中单净流入-净额", "中单净流入", "mid_in_flow"),
        sml_in_flow=amount("小单净流入-净额", "小单净流入", "sml_in_flow"),
        main_in_flow=amount("主力净流入-净额", "主力净流入", "main_in_flow"),
    )


def _merge_records(
    records: tuple[DragonTiger, ...], *, code: str, date: str, endpoint: str
) -> DragonTiger:
    def total(field: str) -> float | None:
        values = [getattr(item, field) for item in records]
        return (
            None
            if all(value is None for value in values)
            else float(sum(value or 0.0 for value in values))
        )

    return DragonTiger(
        date=date,
        code=code,
        reason="；".join(dict.fromkeys(item.reason for item in records if item.reason)),
        net_buy_amount=sum(item.net_buy_amount for item in records),
        buy_amount=total("buy_amount"),
        sell_amount=total("sell_amount"),
        institution_net_buy=total("institution_net_buy"),
        institution_net_sell=total("institution_net_sell"),
        hot_money_net_buy=total("hot_money_net_buy"),
        hot_money_net_sell=total("hot_money_net_sell"),
        institution_seats=tuple(
            dict.fromkeys(seat for item in records for seat in item.institution_seats)
        ),
        hot_money_seats=tuple(
            dict.fromkeys(seat for item in records for seat in item.hot_money_seats)
        ),
        buy_seats=tuple(dict.fromkeys(seat for item in records for seat in item.buy_seats)),
        sell_seats=tuple(
            dict.fromkeys(seat for item in records for seat in item.sell_seats)
        ),
        source="AkShare",
        source_reference=f"{endpoint}:{date}",
    )


def _deduplicate_records(records: list[DragonTiger]) -> list[DragonTiger]:
    """Collapse repeated listing-reason rows without double-counting amounts."""

    grouped: dict[tuple[Any, ...], DragonTiger] = {}
    for record in records:
        key = (
            record.code,
            record.date,
            record.net_buy_amount,
            record.buy_amount,
            record.sell_amount,
            record.institution_net_buy,
            record.institution_net_sell,
            record.hot_money_net_buy,
            record.hot_money_net_sell,
        )
        previous = grouped.get(key)
        if previous is None:
            grouped[key] = record
            continue
        grouped[key] = replace(
            previous,
            reason="；".join(
                dict.fromkeys(
                    item for item in (previous.reason, record.reason) if item
                )
            ),
            institution_seats=tuple(
                dict.fromkeys((*previous.institution_seats, *record.institution_seats))
            ),
            hot_money_seats=tuple(
                dict.fromkeys((*previous.hot_money_seats, *record.hot_money_seats))
            ),
        )
    return list(grouped.values())


def _call_with_timeout(method: Any, kwargs: Mapping[str, Any], *, timeout_seconds: float) -> Any:
    done = Event()
    result: list[Any] = []
    error: list[BaseException] = []

    def run() -> None:
        try:
            result.append(method(**kwargs))
        except BaseException as exc:  # propagate provider exceptions to caller
            error.append(exc)
        finally:
            done.set()

    Thread(target=run, name="second-order-game-akshare", daemon=True).start()
    if not done.wait(timeout_seconds):
        raise AkShareApiError(
            f"AkShare 龙虎榜 request exceeded {timeout_seconds:g} seconds"
        )
    if error:
        raise error[0]
    return result[0] if result else None


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AkShareApiError("AkShare 龙虎榜 request exceeded the 30-second deadline")
    return remaining


def _is_empty_daily_list_error(exc: Exception) -> bool:
    """Recognize AkShare's current empty-result failure from Eastmoney."""
    return isinstance(exc, TypeError) and str(exc).strip() == "'NoneType' object is not subscriptable"


def _rows(payload: Any) -> list[Mapping[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, Mapping):
        return [payload]
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict("records")
        except TypeError:
            records = to_dict(orient="records")
        if isinstance(records, list) and all(isinstance(row, Mapping) for row in records):
            return records
    if isinstance(payload, Iterable) and not isinstance(payload, (str, bytes)):
        records = list(payload)
        if all(isinstance(row, Mapping) for row in records):
            return records
    raise AkShareApiError(f"unexpected AkShare payload type: {type(payload).__name__}")


def _number(row: Mapping[str, Any], *names: str) -> float | None:
    value = _raw(row, *names)
    if value is None or value == "" or value == "-":
        return None
    if isinstance(value, bool):
        raise ValueError(f"numeric field {names[0]} is boolean")
    if isinstance(value, str):
        value = value.replace(",", "").replace("，", "").strip()
        if value.startswith("(") and value.endswith(")"):
            value = "-" + value[1:-1]
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"numeric field {names[0]} is invalid") from exc
    if not math.isfinite(number):
        raise ValueError(f"numeric field {names[0]} is not finite")
    return number


def _split_signed(value: float | None) -> tuple[float | None, float | None]:
    if value is None:
        return None, None
    if value > 0:
        return value, None
    if value < 0:
        return None, abs(value)
    return 0.0, 0.0


def _seat_names(row: Mapping[str, Any], *names: str) -> tuple[str, ...]:
    value = _raw(row, *names)
    if value is None or value == "":
        return ()
    if isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = re.split(r"[,，;；|/、]", str(value))
    return tuple(
        dict.fromkeys(
            item.strip()
            for item in values
            if str(item).strip() and "股通专用" not in str(item)
        )
    )


def _attach_seat_evidence(
    record: DragonTiger,
    buy_rows: Iterable[Mapping[str, Any]],
    sell_rows: Iterable[Mapping[str, Any]],
) -> DragonTiger:
    buy_names: list[str] = []
    sell_names: list[str] = []
    institution_names: list[str] = list(record.institution_seats)
    hot_names: list[str] = list(record.hot_money_seats)
    institution_buy = record.institution_net_buy
    institution_sell = record.institution_net_sell
    institution_values_present = institution_buy is not None or institution_sell is not None
    hot_buy = record.hot_money_net_buy
    hot_sell = record.hot_money_net_sell
    hot_values_present = hot_buy is not None or hot_sell is not None

    directional_rows = [
        *[(row, "buy") for row in buy_rows],
        *[(row, "sell") for row in sell_rows],
    ]
    for row, side in directional_rows:
        name = _text(row, "交易营业部名称", "营业部名称", "席位名称", "seat")
        if not name or "股通专用" in name:
            # Northbound entries are explicitly outside this module's scope.
            continue
        if side == "buy":
            buy_names.append(name)
        else:
            sell_names.append(name)
        is_institution = "机构" in name
        if is_institution:
            institution_names.append(name)
            amount = _number(row, "净额", "净买额", "买入金额", "买入额")
            if amount is not None and not institution_values_present:
                if side == "buy":
                    institution_buy = (institution_buy or 0.0) + abs(amount)
                else:
                    institution_sell = (institution_sell or 0.0) + abs(amount)
        else:
            hot_names.append(name)
            amount = _number(row, "净额", "净买额", "买入金额", "买入额")
            if amount is not None and not hot_values_present:
                if side == "buy":
                    hot_buy = (hot_buy or 0.0) + abs(amount)
                else:
                    hot_sell = (hot_sell or 0.0) + abs(amount)

    return replace(
        record,
        institution_seats=tuple(dict.fromkeys(institution_names)),
        hot_money_seats=tuple(dict.fromkeys(hot_names)),
        buy_seats=tuple(dict.fromkeys(buy_names)),
        sell_seats=tuple(dict.fromkeys(sell_names)),
        institution_net_buy=institution_buy,
        institution_net_sell=institution_sell,
        hot_money_net_buy=hot_buy,
        hot_money_net_sell=hot_sell,
    )


def _raw(row: Mapping[str, Any], *names: str) -> Any:
    normalized = {_normalize_key(key): value for key, value in row.items()}
    for name in names:
        key = _normalize_key(name)
        if key in normalized:
            return normalized[key]
    return None


def _text(row: Mapping[str, Any], *names: str) -> str:
    value = _raw(row, *names)
    return "" if value is None else str(value).strip()


def _normalize_key(value: Any) -> str:
    return re.sub(r"[\s_\-（）()：:]+", "", str(value)).lower()


def _same_code(left: str, right: str) -> bool:
    def bare(value: str) -> str:
        value = value.strip().upper().replace(".", "")
        return value[-6:]

    return bare(left) == bare(right)


def _bare_code(value: str) -> str:
    value = value.strip().upper().replace(".", "")
    return value[-6:]


def _same_date(left: str, right: str) -> bool:
    normalized_left = left.strip().replace("-", "")[:8]
    normalized_right = right.strip().replace("-", "")[:8]
    return normalized_left == normalized_right


def _validate_request(code: str, date: str) -> None:
    if not isinstance(code, str) or not code.strip():
        raise ValueError("code must be a non-empty string")
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("date must use ISO-8601 YYYY-MM-DD format")
    try:
        calendar_date.fromisoformat(date)
    except ValueError as exc:
        raise ValueError("date must be a valid calendar date") from exc


def _validate_pool_date(date: str) -> None:
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("date must use ISO-8601 YYYY-MM-DD format")
    try:
        calendar_date.fromisoformat(date)
    except ValueError as exc:
        raise ValueError("date must be a valid calendar date") from exc


def _as_float(value: float | None) -> float | None:
    return None if value is None else float(value)
