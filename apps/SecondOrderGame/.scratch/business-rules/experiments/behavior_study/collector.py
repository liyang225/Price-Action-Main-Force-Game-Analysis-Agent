"""Offline-testable Futu history collector.

No Futu client is created at import time.  ``collect_dataset`` receives an
already authenticated quote context, making it possible to unit-test all
selection and pagination behavior with a fake context and impossible to
accidentally spend live quota from the study tests.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd


_A_SHARE_CODE = re.compile(r"^(?:SH\.(?:60|68)\d{4}|SZ\.(?:00|30)\d{4})$", re.IGNORECASE)


class ApiError(RuntimeError):
    """Raised when a Futu-like API call returns a non-OK status."""


class RateLimiter:
    """Simple monotonic limiter; call once per symbol's first history page."""

    def __init__(
        self,
        *,
        min_interval_seconds: float = 0.55,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            isinstance(min_interval_seconds, bool)
            or not isinstance(min_interval_seconds, (int, float))
            or not math.isfinite(float(min_interval_seconds))
            or min_interval_seconds < 0
        ):
            raise ValueError("min_interval_seconds must be non-negative")
        self.min_interval_seconds = float(min_interval_seconds)
        self._clock = clock
        self._sleep = sleep
        self._last_call: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last_call is not None:
            remaining = self.min_interval_seconds - (now - self._last_call)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last_call = now


def _as_frame(response: Any, *, operation: str) -> tuple[int, pd.DataFrame, Any]:
    if not isinstance(response, tuple):
        raise ApiError(f"{operation} returned an unexpected response type {type(response).__name__}")
    if len(response) == 3:
        ret_code, data, page_req_key = response
    elif len(response) == 2:
        ret_code, data = response
        page_req_key = None
    else:
        raise ApiError(f"{operation} returned {len(response)} values; expected 2 or 3")
    try:
        ok = int(ret_code) == 0
    except (TypeError, ValueError):
        ok = str(ret_code).upper() in {"0", "RET_OK"}
    if not ok:
        raise ApiError(f"{operation} failed with ret_code={ret_code!r}: {data}")
    if data is None:
        data = pd.DataFrame()
    if not isinstance(data, pd.DataFrame):
        data = pd.DataFrame(data)
    return 0, data.copy(deep=True), page_req_key


def select_configured_plates(plate_list: pd.DataFrame, configured: Sequence[Mapping[str, Any] | str]) -> pd.DataFrame:
    """Select configured industry plates in config order.

    A code is preferred for identity; a name-only entry is matched against
    ``plate_name`` (or Futu's ``name`` alias).  Missing entries are reported as
    an error instead of silently shrinking the sample.
    """

    if not isinstance(plate_list, pd.DataFrame):
        raise TypeError("plate_list must be a DataFrame")
    source = plate_list.copy(deep=True)
    if "plate_name" not in source and "name" in source:
        source = source.rename(columns={"name": "plate_name"})
    if "code" not in source or "plate_name" not in source:
        raise ValueError("plate_list must contain code and plate_name columns")
    source["code"] = source["code"].astype(str).str.upper()
    source["plate_name"] = source["plate_name"].astype(str)
    rows: list[pd.Series] = []
    missing: list[str] = []
    for item in configured:
        if isinstance(item, str):
            wanted_code = item.upper()
            wanted_name = None
        elif isinstance(item, Mapping):
            wanted_code = str(item.get("code", "")).upper() or None
            wanted_name = item.get("name", item.get("plate_name"))
            wanted_name = str(wanted_name) if wanted_name is not None else None
        else:
            raise ValueError("each configured plate must be a code, name, or mapping")
        match = pd.Series(True, index=source.index)
        if wanted_code:
            match &= source["code"].eq(wanted_code)
        elif wanted_name:
            match &= source["plate_name"].eq(wanted_name)
        else:
            raise ValueError("configured plate requires code or name")
        candidates = source.loc[match]
        if candidates.empty:
            missing.append(wanted_code or wanted_name or "<unknown>")
            continue
        rows.append(candidates.iloc[0])
    if missing:
        raise ValueError(f"configured industry plates were not found: {', '.join(missing)}")
    selected = pd.DataFrame(rows).reset_index(drop=True)
    selected["config_order"] = range(len(selected))
    return selected


def _column(frame: pd.DataFrame, *names: str) -> pd.Series:
    for name in names:
        if name in frame:
            return frame[name]
    return pd.Series(pd.NA, index=frame.index)


def is_eligible_a_share(
    snapshot: pd.DataFrame,
    *,
    listed_by: str | None = "2022-01-01",
    required_status: str = "NORMAL",
    exclude_name_patterns: Sequence[str] = ("ST", "退"),
) -> pd.Series:
    """Return a mask for liquid, currently normal SH/SZ A-shares."""

    if "code" not in snapshot:
        raise ValueError("market snapshot must contain code")
    codes = snapshot["code"].astype(str).str.upper()
    mask = codes.map(lambda code: bool(_A_SHARE_CODE.fullmatch(code)))
    names = _column(snapshot, "name", "stock_name").astype(str)
    for pattern in exclude_name_patterns:
        mask &= ~names.str.contains(str(pattern), case=False, regex=False, na=False)
    status_columns = ("listing_status", "status", "stock_status", "sec_status")
    available_status = next((column for column in status_columns if column in snapshot), None)
    if available_status is None:
        raise ValueError("market snapshot must contain a listing status column")
    status = snapshot[available_status].astype(str).str.upper()
    mask &= status.eq(required_status.upper())
    if listed_by is not None:
        cutoff = pd.Timestamp(listed_by)
        listing_dates = pd.to_datetime(_column(snapshot, "listing_date", "listing_date_str"), errors="coerce")
        mask &= listing_dates.le(cutoff)
    values = pd.to_numeric(_column(snapshot, "circular_market_val"), errors="coerce")
    mask &= values.gt(0)
    return mask.astype(bool)


def rank_current_eligible(
    snapshot: pd.DataFrame,
    *,
    top_n: int,
    listed_by: str | None = "2022-01-01",
    required_status: str = "NORMAL",
    exclude_name_patterns: Sequence[str] = ("ST", "退"),
) -> pd.DataFrame:
    """Filter and rank current eligible stocks by circulating market value."""

    if top_n < 1:
        raise ValueError("top_n must be positive")
    result = snapshot.loc[
        is_eligible_a_share(
            snapshot,
            listed_by=listed_by,
            required_status=required_status,
            exclude_name_patterns=exclude_name_patterns,
        )
    ].copy()
    result["circular_market_val"] = pd.to_numeric(result["circular_market_val"], errors="coerce")
    result["code"] = result["code"].astype(str).str.upper()
    return result.sort_values(
        ["circular_market_val", "code"], ascending=[False, True], kind="mergesort"
    ).head(top_n).reset_index(drop=True)


def fetch_history_paginated(
    quote_ctx: Any,
    code: str,
    *,
    start: str,
    end: str,
    ktype: Any = "K_DAY",
    autype: Any = "QFQ",
    max_count: int = 1000,
    rate_limiter: RateLimiter | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch all pages for one code; only the first page consumes the limiter."""

    if type(max_count) is not int or max_count < 1:
        raise ValueError("max_count must be positive")
    limiter = rate_limiter or RateLimiter()
    page_req_key = None
    frames: list[pd.DataFrame] = []
    page_count = 0
    seen_page_keys: set[str] = set()
    while True:
        if page_req_key is not None:
            page_token = repr(page_req_key)
            if page_token in seen_page_keys:
                raise ApiError(f"request_history_kline({code}) repeated page_req_key={page_req_key!r}")
            seen_page_keys.add(page_token)
        if page_count == 0:
            limiter.wait()
        kwargs: dict[str, Any] = {
            "code": code,
            "start": start,
            "end": end,
            "ktype": ktype,
            "max_count": max_count,
            "page_req_key": page_req_key,
        }
        if autype is not None:
            kwargs["autype"] = autype
        response = quote_ctx.request_history_kline(**kwargs)
        _, frame, page_req_key = _as_frame(response, operation=f"request_history_kline({code})")
        page_count += 1
        if not frame.empty:
            frames.append(frame)
        if page_req_key is None:
            break
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not result.empty:
        timestamp_column = "time_key" if "time_key" in result else "date" if "date" in result else None
        if timestamp_column:
            result["date"] = pd.to_datetime(result[timestamp_column], errors="coerce").dt.normalize()
            result = result.sort_values("date", kind="mergesort").reset_index(drop=True)
    metadata = {
        "code": code,
        "requested_start": start,
        "requested_end": end,
        "page_count": page_count,
        "row_count": len(result),
        "first_available_time": result["date"].min().isoformat() if not result.empty and result["date"].notna().any() else None,
        "last_available_time": result["date"].max().isoformat() if not result.empty and result["date"].notna().any() else None,
    }
    return result, metadata


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except ValueError:
            pass
    return value


def write_collection(
    data: pd.DataFrame,
    output_dir: str | Path,
    *,
    config: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write deterministic compressed observations and an audit manifest."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    csv_path = destination / "daily_ohlcv.csv.gz"
    manifest_path = destination / "manifest.json"
    data.to_csv(
        csv_path,
        index=False,
        compression={"method": "gzip", "mtime": 0},
        encoding="utf-8",
    )
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    manifest: dict[str, Any] = {
        "schema_version": "1",
        "row_count": int(len(data)),
        "column_names": list(data.columns),
        "sha256": digest,
        "config": _jsonable(config),
    }
    if metadata:
        manifest.update(_jsonable(metadata))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def _api_market_argument(value: Any, api_constants: Mapping[str, Any] | None) -> Any:
    if api_constants and isinstance(value, str):
        return api_constants.get(value, value)
    return value


def collect_dataset(
    quote_ctx: Any,
    config: Mapping[str, Any],
    output_dir: str | Path,
    *,
    api_constants: Mapping[str, Any] | None = None,
    rate_limiter: RateLimiter | None = None,
) -> dict[str, Any]:
    """Select configured plates/stocks and collect K_DAY data.

    API errors are retained per instrument in the manifest; one unavailable
    code does not erase successfully collected observations.
    """

    configured = config.get("industry_plates", ())
    if not configured:
        raise ValueError("config must contain industry_plates")
    markets = config.get("plate_markets", ("SH", "SZ"))
    plate_frames: list[pd.DataFrame] = []
    for market in markets:
        market_arg = _api_market_argument(market, api_constants)
        plate_class = _api_market_argument("INDUSTRY", api_constants)
        try:
            response = quote_ctx.get_plate_list(market=market_arg, plate_class=plate_class)
        except TypeError:
            try:
                response = quote_ctx.get_plate_list(market_arg, plate_class)
            except TypeError:
                response = quote_ctx.get_plate_list()
        _, frame, _ = _as_frame(response, operation="get_plate_list")
        plate_frames.append(frame)
    selected_plates = select_configured_plates(pd.concat(plate_frames, ignore_index=True), configured)

    members_by_plate: dict[str, pd.DataFrame] = {}
    selection_errors: list[dict[str, str]] = []
    all_codes: set[str] = set()
    for row in selected_plates.itertuples(index=False):
        try:
            try:
                response = quote_ctx.get_plate_stock(plate_code=row.code)
            except TypeError:
                response = quote_ctx.get_plate_stock(row.code)
            _, members, _ = _as_frame(response, operation=f"get_plate_stock({row.code})")
            if "code" not in members and "stock_code" in members:
                members = members.rename(columns={"stock_code": "code"})
            members["code"] = members["code"].astype(str).str.upper()
            members_by_plate[row.code] = members.drop_duplicates("code")
            all_codes.update(members["code"])
        except Exception as error:
            members_by_plate[row.code] = pd.DataFrame(columns=["code"])
            selection_errors.append({"code": row.code, "error": str(error)})

    snapshot_frames: list[pd.DataFrame] = []
    snapshot_batch_size = config.get("snapshot_batch_size", 400)
    if type(snapshot_batch_size) is not int or snapshot_batch_size < 1:
        raise ValueError("snapshot_batch_size must be a positive integer")
    codes = sorted(all_codes)
    for offset in range(0, len(codes), snapshot_batch_size):
        batch = codes[offset : offset + snapshot_batch_size]
        response = quote_ctx.get_market_snapshot(batch)
        _, snapshot, _ = _as_frame(response, operation="get_market_snapshot")
        snapshot_frames.append(snapshot)
    snapshot = pd.concat(snapshot_frames, ignore_index=True) if snapshot_frames else pd.DataFrame(columns=["code"])

    selected_stocks: list[pd.DataFrame] = []
    for row in selected_plates.itertuples(index=False):
        members = members_by_plate[row.code]
        if members.empty:
            continue
        candidates = snapshot.loc[snapshot["code"].astype(str).str.upper().isin(members["code"])].copy()
        ranked = rank_current_eligible(
            candidates,
            top_n=int(config.get("top_n_per_plate", 5)),
            listed_by=config.get("listed_by", "2022-01-01"),
            required_status=str(config.get("required_status", "NORMAL")),
            exclude_name_patterns=tuple(config.get("exclude_name_patterns", ("ST", "退"))),
        )
        if not ranked.empty:
            ranked["plate_code"] = row.code
            ranked["plate_name"] = row.plate_name
            ranked["plate_order"] = row.config_order
            selected_stocks.append(ranked)
    stock_selection = pd.concat(selected_stocks, ignore_index=True) if selected_stocks else pd.DataFrame()

    instrument_rows: list[dict[str, Any]] = []
    if not stock_selection.empty:
        for code, group in stock_selection.groupby("code", sort=True):
            ordered = group.sort_values("plate_order", kind="mergesort")
            instrument_rows.append(
                {
                    "code": code,
                    "instrument_type": "stock",
                    "plate_codes": ordered["plate_code"].astype(str).drop_duplicates().tolist(),
                    "plate_names": ordered["plate_name"].astype(str).drop_duplicates().tolist(),
                    "primary_plate_code": str(ordered.iloc[0]["plate_code"]),
                }
            )
    for row in selected_plates.itertuples(index=False):
        instrument_rows.append(
            {
                "code": row.code,
                "instrument_type": "sector",
                "plate_codes": [row.code],
                "plate_names": [row.plate_name],
                "primary_plate_code": row.code,
            }
        )
    benchmark_code = config.get("benchmark_code")
    if benchmark_code:
        instrument_rows.append(
            {
                "code": str(benchmark_code).upper(),
                "instrument_type": "benchmark",
                "plate_codes": [],
                "plate_names": [],
                "primary_plate_code": None,
            }
        )

    observations: list[pd.DataFrame] = []
    instrument_metadata: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = list(selection_errors)
    limiter = rate_limiter or RateLimiter(
        min_interval_seconds=float(config.get("first_page_interval_seconds", 0.55))
    )
    for instrument in instrument_rows:
        try:
            history, item_meta = fetch_history_paginated(
                quote_ctx,
                instrument["code"],
                start=str(config.get("start_date")),
                end=str(config.get("end_date")),
                ktype=_api_market_argument("K_DAY", api_constants),
                autype=_api_market_argument(
                    "QFQ" if instrument["instrument_type"] == "stock" else "NONE",
                    api_constants,
                ),
                max_count=int(config.get("max_count", 1000)),
                rate_limiter=limiter,
            )
            if not history.empty:
                history["code"] = instrument["code"]
                history["instrument_type"] = instrument["instrument_type"]
                history["plate_codes"] = json.dumps(instrument["plate_codes"], ensure_ascii=False)
                history["plate_names"] = json.dumps(instrument["plate_names"], ensure_ascii=False)
                history["primary_plate_code"] = instrument.get("primary_plate_code")
                leading = ["code", "instrument_type", "primary_plate_code", "plate_codes", "plate_names"]
                history = history[leading + [column for column in history.columns if column not in leading]]
                observations.append(history)
            item_meta.update({"instrument_type": instrument["instrument_type"], "status": "ok"})
            instrument_metadata.append(item_meta)
        except Exception as error:
            errors.append({"code": instrument["code"], "error": str(error)})
            instrument_metadata.append(
                {"code": instrument["code"], "instrument_type": instrument["instrument_type"], "status": "error", "error": str(error)}
            )
    data = pd.concat(observations, ignore_index=True) if observations else pd.DataFrame()
    return write_collection(
        data,
        output_dir,
        config=config,
        metadata={
            "selected_plates": selected_plates[["code", "plate_name"]].to_dict(orient="records"),
            "selected_stock_count": int(stock_selection["code"].nunique()) if not stock_selection.empty else 0,
            "instruments": instrument_metadata,
            "errors": errors,
        },
    )


# Descriptive alias for callers that prefer the API's terminology.
collect_futu = collect_dataset


__all__ = [
    "ApiError",
    "RateLimiter",
    "collect_dataset",
    "collect_futu",
    "fetch_history_paginated",
    "is_eligible_a_share",
    "rank_current_eligible",
    "select_configured_plates",
    "write_collection",
]
