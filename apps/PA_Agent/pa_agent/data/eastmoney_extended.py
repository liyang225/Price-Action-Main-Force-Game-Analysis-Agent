"""East Money single-stock extended APIs (资金流/F10/公告/研报等).

Discovered via ``scripts/probe_single_stock_apis*.py`` (2026-06).
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from pa_agent.data.eastmoney_client import (
    EastMoneyTransientError,
    _QUOTE_HOSTS,
    _REFERER_KLINE,
    _UT,
    _get_json_on_hosts,
    stock_secid,
)

logger = logging.getLogger(__name__)

_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_EMWEB_BASE = "https://emweb.securities.eastmoney.com"
_NOTICE_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
_NEWS_URL = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
_REPORT_URL = "https://reportapi.eastmoney.com/report/list"

# push2 ``ulist.np`` 资金流字段（与行情页「资金流向」一致）
_MONEY_FLOW_FIELDS = (
    "f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124"
)
# stock/get 估值/指标扩展字段
_VALUATION_FIELDS = (
    "f57,f58,f43,f116,f117,f162,f167,f168,f169,f170,f46,f44,f45,f47,f48,f60,"
    "f84,f85,f92,f103,f104,f105,f106,f107,f108,f109,f110,f111,f112,f113,f114,"
    "f115,f130,f131,f132,f133,f134,f135,f136,f137,f138,f139,f140,f141,f142,"
    "f143,f144,f145,f146,f147,f148,f149,f150,f151,f152,f153,f154,f155,f156,"
    "f157,f158,f159,f160,f161,f163,f164,f165,f166,f171,f172,f173,f174,f175,"
    "f176,f177,f178,f179,f180,f181,f182,f183,f184,f185,f186,f187,f188,f189,"
    "f190,f191,f192,f193,f194,f195,f196,f197,f198,f199,f200"
)

# key -> (reportName, sortColumns, filter_template with {code} and optional {secucode})
_REPORT_NAMES: dict[str, tuple[str, str, str]] = {
    "valuation": ("RPT_VALUEANALYSIS_DET", "TRADE_DATE", '(SECURITY_CODE="{code}")'),
    "top_holders": ("RPT_F10_EH_HOLDERS", "END_DATE", '(SECURITY_CODE="{code}")'),
    "free_holders": ("RPT_F10_EH_FREEHOLDERS", "END_DATE", '(SECURITY_CODE="{code}")'),
    "finance_main": ("RPT_F10_FINANCE_MAINFINADATA", "REPORT_DATE", '(SECURITY_CODE="{code}")'),
    "industry": ("RPT_STOCK_INDUSTRY_STA", "TRADE_DATE", '(SECURITY_CODE="{code}")'),
    "block_trade": ("RPT_BLOCKTRADE_STA", "TRADE_DATE", '(SECURITY_CODE="{code}")'),
    "org_hold": ("RPT_MAIN_ORGHOLD", "REPORT_DATE", '(SECURITY_CODE="{code}")'),
    "lhb_buy": ("RPT_BILLBOARD_DAILYDETAILSBUY", "TRADE_DATE", '(SECURITY_CODE="{code}")'),
    "lhb_sell": ("RPT_BILLBOARD_DAILYDETAILSSELL", "TRADE_DATE", '(SECURITY_CODE="{code}")'),
    "holder_num": ("RPT_F10_EH_HOLDERNUM", "END_DATE", '(SECURITY_CODE="{code}")'),
    "margin": ("RPTA_WEB_RZRQ_GGMX", "DATE", '(SECUCODE="{secucode}")'),
    # data.eastmoney.com 门户 JS 逆向（scripts/scrape_portal_js_reports.py）
    "stock_evaluate": ("RPT_DMSK_TS_STOCKEVALUATE", "TRADE_DATE", '(SECURITY_CODE="{code}")'),
    "stock_comment": ("RPT_STOCK_TRENDVOLUME_COMMENT", "TRADE_DATE", '(SECURITY_CODE="{code}")'),
    "participation": ("RPT_STOCK_PARTICIPATION", "TRADE_DATE", '(SECURITY_CODE="{code}")'),
    "market_focus": ("RPT_STOCK_MARKETFOCUS", "TRADE_DATE", '(SECURITY_CODE="{code}")'),
    "margin_trend": ("RPT_STOCK_MARGINTREND", "TRADE_DATE", '(SECURITY_CODE="{code}")'),
    "holder_trade": ("RPT_SHARE_HOLDER_INCREASE", "NOTICE_DATE", '(SECURITY_CODE="{code}")'),
    "pledge_summary": ("RPT_CSDC_LIST_NEWEST", "TRADE_DATE", '(SECURITY_CODE="{code}")'),
    "mutual_hold": ("RPT_MUTUAL_HOLDRANK_NEW", "HOLD_DATE", '(SECURITY_CODE="{code}")'),
    "mutual_deal": ("RPT_MUTUAL_TOP10DEAL", "TRADE_DATE", '(SECURITY_CODE="{code}")'),
    "fin_analysis": ("RPT_F10_FINANALYSIS", "REPORT_DATE", '(SECURITY_CODE="{code}")'),
    "org_survey": ("RPT_ORG_SURVEYNEW", "NOTICE_DATE", '(SECURITY_CODE="{code}")'),
}

_BOARD_TAG_FIELDS = "f127,f128,f129"

_EMWEB_PAGES = frozenset(
    {
        "CompanySurvey",
        "CoreConception",
        "OperationsRequired",
        "BusinessAnalysis",
        "CompanyManagement",
        "CompanyBigNews",
        "ShareholderResearch",
        "CapitalStockStructure",
        "BonusFinancing",
    }
)

_BOARD_FLOW_FIELDS = "f12,f14,f2,f3,f62,f184,f66,f69"

_FFLOW_FIELDS1 = "f1,f2,f3,f7"
_FFLOW_FIELDS2 = (
    "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
)
_FFLOW_DAILY_NAMES = (
    "date",
    "main_net",
    "small_net",
    "medium_net",
    "large_net",
    "super_large_net",
    "main_net_pct",
    "small_net_pct",
    "medium_net_pct",
    "large_net_pct",
    "super_large_net_pct",
    "close",
    "change_pct",
    "unknown_1",
    "unknown_2",
)
_FFLOW_INTRADAY_NAMES = (
    "time",
    "main_net",
    "small_net",
    "medium_net",
    "large_net",
    "super_large_net",
)


def _symbol_code(symbol: str) -> str:
    return symbol[-6:] if len(symbol) > 6 else symbol.strip()


def _em_code(symbol: str) -> str:
    code = _symbol_code(symbol)
    if code.startswith(("5", "6", "9")):
        return f"SH{code}"
    return f"SZ{code}"


def _secucode(symbol: str) -> str:
    code = _symbol_code(symbol)
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _parse_fflow_klines(
    klines: list[str],
    *,
    field_names: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < len(field_names):
            continue
        row = {field_names[i]: parts[i] for i in range(len(field_names))}
        rows.append(row)
    return rows


def _parse_jsonp(text: str) -> Any:
    text = text.strip()
    m = re.match(r"^[^(]+\((.*)\)\s*;?\s*$", text, flags=re.DOTALL)
    if m:
        return json.loads(m.group(1))
    return json.loads(text)


def datacenter_query(
    report_name: str,
    *,
    filter_expr: str,
    sort_columns: str = "TRADE_DATE",
    page_number: int = 1,
    page_size: int = 20,
    sort_types: str = "-1",
) -> list[dict[str, Any]]:
    """Query ``datacenter-web.eastmoney.com/api/data/v1/get``."""
    params = {
        "reportName": report_name,
        "columns": "ALL",
        "filter": filter_expr,
        "pageNumber": str(page_number),
        "pageSize": str(page_size),
        "sortTypes": sort_types,
        "sortColumns": sort_columns,
    }
    try:
        from curl_cffi import requests as req

        extra = {"impersonate": "chrome120"}
    except ImportError:
        import requests as req

        extra = {}
    headers = {"Referer": "https://data.eastmoney.com/", "User-Agent": _UT}
    r = req.get(_DATACENTER_URL, params=params, headers=headers, timeout=15, **extra)
    j = r.json()
    if not j.get("success"):
        msg = str(j.get("message") or "datacenter failed")
        logger.debug("datacenter %s: %s", report_name, msg)
        return []
    return list((j.get("result") or {}).get("data") or [])


def datacenter_for_symbol(
    key: str,
    symbol: str,
    *,
    page_size: int = 20,
) -> list[dict[str, Any]]:
    """Named report shortcut (see ``_REPORT_NAMES``)."""
    report_name, sort_col, filter_tpl = _REPORT_NAMES[key]
    code = _symbol_code(symbol)
    filter_expr = filter_tpl.format(code=code, secucode=_secucode(code))
    return datacenter_query(
        report_name,
        filter_expr=filter_expr,
        sort_columns=sort_col,
        page_size=page_size,
    )


def fetch_money_flow_snapshot(symbol: str) -> dict[str, Any] | None:
    """当日资金流向快照（主力/超大/大/中/小单净流入及占比）。"""
    secid = stock_secid(symbol)
    params = {
        "fltt": "2",
        "secids": secid,
        "fields": _MONEY_FLOW_FIELDS,
        "ut": _UT,
    }
    try:
        data = _get_json_on_hosts(
            _QUOTE_HOSTS,
            "/api/qt/ulist.np/get",
            params,
            timeout=10.0,
            host_kind="quote",
            referer=_REFERER_KLINE,
            max_rounds=1,
            max_hosts=3,
        )
        diff = (data.get("data") or {}).get("diff") or []
        if not diff:
            return None
        row = diff[0]
        return {
            "main_net": row.get("f62"),
            "main_net_pct": row.get("f184"),
            "super_large_net": row.get("f66"),
            "super_large_pct": row.get("f69"),
            "large_net": row.get("f72"),
            "large_pct": row.get("f75"),
            "medium_net": row.get("f78"),
            "medium_pct": row.get("f81"),
            "small_net": row.get("f84"),
            "small_pct": row.get("f87"),
            "updated_at": row.get("f124"),
            "raw": row,
        }
    except EastMoneyTransientError as exc:
        logger.debug("money flow snapshot failed: %s", exc)
        return None


def fetch_valuation_fields(symbol: str) -> dict[str, Any] | None:
    """PE/PB/市值等扩展字段（``stock/get`` 窄 fields）。"""
    params = {
        "secid": stock_secid(symbol),
        "ut": _UT,
        "fltt": "2",
        "invt": "2",
        "fields": _VALUATION_FIELDS,
    }
    try:
        data = _get_json_on_hosts(
            _QUOTE_HOSTS,
            "/api/qt/stock/get",
            params,
            timeout=10.0,
            host_kind="quote",
            referer=_REFERER_KLINE,
            max_rounds=1,
            max_hosts=2,
        )
        payload = data.get("data") or {}
        return payload if payload else None
    except EastMoneyTransientError as exc:
        logger.debug("valuation fields failed: %s", exc)
        return None


def fetch_emweb_page(page: str, symbol: str) -> dict[str, Any] | None:
    """F10 ``PageAjax``（概念/经营/股东研究等）。"""
    if page not in _EMWEB_PAGES:
        raise ValueError(f"unsupported emweb page: {page}")
    try:
        from curl_cffi import requests as req

        extra = {"impersonate": "chrome120"}
    except ImportError:
        import requests as req

        extra = {}
    url = f"{_EMWEB_BASE}/PC_HSF10/{page}/PageAjax"
    r = req.get(
        url,
        params={"code": _em_code(symbol)},
        headers={"Referer": "https://emweb.securities.eastmoney.com/"},
        timeout=15,
        **extra,
    )
    if r.status_code != 200:
        return None
    try:
        j = r.json()
    except json.JSONDecodeError:
        return None
    if j.get("status") == -1:
        return None
    return j


def fetch_company_survey(symbol: str) -> dict[str, Any] | None:
    """F10 公司概况（``emweb`` PageAjax）。"""
    return fetch_emweb_page("CompanySurvey", symbol)


def fetch_core_conception(symbol: str) -> dict[str, Any] | None:
    """F10 核心题材 / 所属板块（``ssbk``、``hxtc``）。"""
    return fetch_emweb_page("CoreConception", symbol)


def fetch_shareholder_research(symbol: str) -> dict[str, Any] | None:
    """F10 股东研究（户数趋势、机构持仓日期等元数据）。"""
    return fetch_emweb_page("ShareholderResearch", symbol)


def fetch_operations_required(symbol: str) -> dict[str, Any] | None:
    """F10 经营必读（板块/题材/股东户数/融资融券/龙虎榜/大宗/公告等聚合）。"""
    return fetch_emweb_page("OperationsRequired", symbol)


def fetch_capital_stock_structure(symbol: str) -> dict[str, Any] | None:
    """F10 股本结构（限售解禁、股本变动等）。"""
    return fetch_emweb_page("CapitalStockStructure", symbol)


def fetch_bonus_financing(symbol: str) -> dict[str, Any] | None:
    """F10 分红融资（分红、增发、配股记录）。"""
    return fetch_emweb_page("BonusFinancing", symbol)


def fetch_business_analysis(symbol: str) -> dict[str, Any] | None:
    """F10 经营分析（主营构成、经营评述）。"""
    return fetch_emweb_page("BusinessAnalysis", symbol)


def fetch_company_management(symbol: str) -> dict[str, Any] | None:
    """F10 公司高管（高管列表、持股变动摘要）。"""
    return fetch_emweb_page("CompanyManagement", symbol)


def fetch_company_big_news(symbol: str) -> dict[str, Any] | None:
    """F10 公司大事（重大事项、质押、担保、龙虎榜明细等）。"""
    return fetch_emweb_page("CompanyBigNews", symbol)


def _board_secid(board_code: str) -> str:
    raw = str(board_code).strip().upper().replace("BK", "")
    return f"90.BK{raw.zfill(4)}"


def fetch_board_money_flow(board_code: str) -> dict[str, Any] | None:
    """所属板块当日主力净流入（``ulist.np``，板块 secid=90.BKxxxx）。"""
    secid = _board_secid(board_code)
    params = {
        "fltt": "2",
        "secids": secid,
        "fields": _BOARD_FLOW_FIELDS,
        "ut": _UT,
    }
    try:
        data = _get_json_on_hosts(
            _QUOTE_HOSTS,
            "/api/qt/ulist.np/get",
            params,
            timeout=10.0,
            host_kind="quote",
            referer="https://data.eastmoney.com/bkzj/",
            max_rounds=1,
            max_hosts=2,
        )
        diff = list((data.get("data") or {}).get("diff") or [])
        if not diff:
            return None
        row = diff[0]
        return {
            "board_code": board_code,
            "board_name": row.get("f14"),
            "pct_chg": row.get("f3"),
            "main_net": row.get("f62"),
            "main_net_pct": row.get("f184"),
            "super_large_net": row.get("f66"),
            "large_net": row.get("f69"),
            "raw": row,
        }
    except EastMoneyTransientError as exc:
        logger.debug("board money flow failed %s: %s", board_code, exc)
        return None


def fetch_stock_board_money_flows(
    symbol: str,
    boards: list[dict[str, Any]] | None = None,
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Top-ranked industry/concept boards with当日主力净流入."""
    if boards is None:
        ops = fetch_operations_required(symbol)
        boards = list((ops or {}).get("ssbk") or [])
    if not boards:
        return []
    ranked = sorted(
        boards,
        key=lambda b: int(b.get("BOARD_RANK") or 999),
    )
    flows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for board in ranked:
        code = str(board.get("BOARD_CODE") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        snap = fetch_board_money_flow(code)
        if snap is None:
            continue
        snap["board_name"] = snap.get("board_name") or board.get("BOARD_NAME")
        snap["board_rank"] = board.get("BOARD_RANK")
        flows.append(snap)
        if len(flows) >= limit:
            break
    return flows


def fetch_valuation_summary(symbol: str) -> dict[str, Any] | None:
    """PE/PB/市值/换手等摘要（由 ``stock/get`` 估值字段解析）。"""
    raw = fetch_valuation_fields(symbol)
    if not raw:
        return None
    return {
        "price": raw.get("f43"),
        "high": raw.get("f44"),
        "low": raw.get("f45"),
        "open": raw.get("f46"),
        "prev_close": raw.get("f60"),
        "volume": raw.get("f47"),
        "amount": raw.get("f48"),
        "total_mv": raw.get("f116"),
        "float_mv": raw.get("f117"),
        "pe_dynamic": raw.get("f162"),
        "pb": raw.get("f167"),
        "turnover_pct": raw.get("f168"),
        "volume_ratio": raw.get("f50") if "f50" in raw else raw.get("f10"),
        "raw": raw,
    }


def fetch_stock_board_tags(symbol: str) -> dict[str, Any] | None:
    """所属行业/地域/概念（``stock/get`` 的 f127–f129，替代已 404 的 ``bk/get``）。"""
    params = {
        "secid": stock_secid(symbol),
        "ut": _UT,
        "fltt": "2",
        "invt": "2",
        "fields": _BOARD_TAG_FIELDS,
    }
    try:
        data = _get_json_on_hosts(
            _QUOTE_HOSTS,
            "/api/qt/stock/get",
            params,
            timeout=10.0,
            host_kind="quote",
            referer=_REFERER_KLINE,
            max_rounds=1,
            max_hosts=2,
        )
        payload = data.get("data") or {}
        if not payload:
            return None
        concepts_raw = payload.get("f129") or ""
        concepts = [c.strip() for c in str(concepts_raw).split(",") if c.strip()]
        return {
            "industry": payload.get("f127"),
            "region": payload.get("f128"),
            "concepts": concepts,
            "raw": payload,
        }
    except EastMoneyTransientError as exc:
        logger.debug("board tags failed: %s", exc)
        return None


def fetch_stock_announcements(
    symbol: str,
    *,
    page_size: int = 20,
    page_index: int = 1,
) -> list[dict[str, Any]]:
    """交易所公告列表。"""
    code = symbol[-6:] if len(symbol) > 6 else symbol.strip()
    params = {
        "page_size": str(page_size),
        "page_index": str(page_index),
        "ann_type": "A",
        "client_source": "web",
        "stock_list": code,
    }
    try:
        from curl_cffi import requests as req

        extra = {"impersonate": "chrome120"}
    except ImportError:
        import requests as req

        extra = {}
    r = req.get(
        _NOTICE_URL,
        params=params,
        headers={"Referer": "https://data.eastmoney.com/notices/"},
        timeout=15,
        **extra,
    )
    j = r.json()
    if not j.get("success", True) and not j.get("data"):
        return []
    payload = j.get("data") if isinstance(j.get("data"), dict) else j
    return list(payload.get("list") or j.get("list") or [])


def fetch_stock_news(
    symbol: str,
    *,
    page_size: int = 20,
    page_index: int = 1,
) -> list[dict[str, Any]]:
    """个股新闻（需 ``req_trace``）。"""
    code = symbol[-6:] if len(symbol) > 6 else symbol.strip()
    params = {
        "client": "web",
        "biz": "web_quote",
        "column": "350",
        "page_index": str(page_index),
        "page_size": str(page_size),
        "stockcode": code,
        "req_trace": "1",
    }
    try:
        from curl_cffi import requests as req

        extra = {"impersonate": "chrome120"}
    except ImportError:
        import requests as req

        extra = {}
    r = req.get(
        _NEWS_URL,
        params=params,
        headers={"Referer": "https://quote.eastmoney.com/"},
        timeout=15,
        **extra,
    )
    j = r.json()
    data = j.get("data") or {}
    return list(data.get("list") or [])


def fetch_research_reports(
    symbol: str,
    *,
    page_size: int = 20,
    begin_time: str = "2024-01-01",
) -> list[dict[str, Any]]:
    """个股研报列表（JSONP）。"""
    code = symbol[-6:] if len(symbol) > 6 else symbol.strip()
    params = {
        "cb": "jQuery",
        "industryCode": "*",
        "pageSize": str(page_size),
        "industry": "*",
        "rating": "*",
        "ratingChange": "*",
        "beginTime": begin_time,
        "endTime": "2099-01-01",
        "pageNo": "1",
        "fields": "",
        "qType": "0",
        "orgCode": "",
        "code": code,
        "p": "1",
        "pageNum": "1",
        "pageNumber": "1",
    }
    try:
        from curl_cffi import requests as req

        extra = {"impersonate": "chrome120"}
    except ImportError:
        import requests as req

        extra = {}
    r = req.get(
        _REPORT_URL,
        params=params,
        headers={"Referer": "https://data.eastmoney.com/report/stock.jshtml"},
        timeout=15,
        **extra,
    )
    try:
        j = _parse_jsonp(r.text)
    except (json.JSONDecodeError, TypeError):
        return []
    return list(j.get("data") or [])


def fetch_money_flow_klines(
    symbol: str,
    *,
    klt: str = "101",
    lmt: int = 60,
) -> list[dict[str, Any]]:
    """资金流向 K 线。

    ``klt=1`` 当日分时；``klt=101`` 日 K（历史走 push2his）。
    """
    secid = stock_secid(symbol)
    params = {
        "lmt": str(lmt),
        "klt": klt,
        "secid": secid,
        "fields1": _FFLOW_FIELDS1,
        "fields2": _FFLOW_FIELDS2,
        "ut": _UT,
    }
    referer = "https://data.eastmoney.com/zjlx/"
    if klt == "1":
        hosts = ("push2.eastmoney.com",)
        path = "/api/qt/stock/fflow/kline/get"
        names = _FFLOW_INTRADAY_NAMES
    else:
        # push2 对 daykline 在大 lmt 时仅返回 1 条，历史日 K 必须走 push2his。
        hosts = ("push2his.eastmoney.com",)
        path = "/api/qt/stock/fflow/daykline/get"
        names = _FFLOW_DAILY_NAMES
    try:
        data = _get_json_on_hosts(
            hosts,
            path,
            params,
            timeout=12.0,
            host_kind="kline",
            referer=referer,
            max_rounds=1,
            max_hosts=1,
        )
        klines = list((data.get("data") or {}).get("klines") or [])
        return _parse_fflow_klines(klines, field_names=names)
    except EastMoneyTransientError as exc:
        logger.debug("money flow klines failed: %s", exc)
        return []


_PORTAL_DC_KEYS: tuple[str, ...] = (
    "stock_evaluate",
    "stock_comment",
    "participation",
    "market_focus",
    "margin_trend",
    "fin_analysis",
    "mutual_deal",
    "pledge_summary",
    "mutual_hold",
    "org_survey",
    "holder_trade",
)


def fetch_portal_datacenter_bundle(symbol: str) -> dict[str, list[dict[str, Any]]]:
    """Homepage portal datacenter reports (千股千评/质押/港通/调研等)."""
    bundle: dict[str, list[dict[str, Any]]] = {}

    def _fetch_key(key: str) -> tuple[str, list[dict[str, Any]]]:
        page_size = 3 if key == "holder_trade" else 1
        return key, datacenter_for_symbol(key, symbol, page_size=page_size)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_fetch_key, key) for key in _PORTAL_DC_KEYS]
        for fut in as_completed(futures):
            try:
                key, rows = fut.result()
                bundle[key] = rows
            except Exception as exc:  # noqa: BLE001
                logger.debug("portal bundle key failed: %s", exc)
    for key in _PORTAL_DC_KEYS:
        bundle.setdefault(key, [])
    return bundle


_COMPACT_CTX_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_COMPACT_CTX_TTL_S = 60.0
_COMPACT_PARALLEL_WORKERS = 8


def clear_compact_stock_context_cache(symbol: str | None = None) -> None:
    """Drop cached compact context (one symbol or all)."""
    if symbol is None:
        _COMPACT_CTX_CACHE.clear()
        return
    _COMPACT_CTX_CACHE.pop(_symbol_code(symbol), None)


def fetch_compact_stock_context(symbol: str, *, use_cache: bool = True) -> dict[str, Any]:
    """Deep lightweight bundle for AI prompt (F10 + datacenter + 板块资金)."""
    code = _symbol_code(symbol)
    if use_cache:
        now = time.monotonic()
        cached = _COMPACT_CTX_CACHE.get(code)
        if cached and now - cached[0] < _COMPACT_CTX_TTL_S:
            return dict(cached[1])
    ctx = _build_compact_stock_context(code)
    if use_cache:
        _COMPACT_CTX_CACHE[code] = (time.monotonic(), ctx)
    return dict(ctx)


def _apply_operations_fields(ctx: dict[str, Any], ops: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Derive F10 OperationsRequired sub-fields; return ssbk board list."""
    boards: list[dict[str, Any]] = []
    ctx["operations"] = ops
    if not ops:
        ctx.setdefault("boards", [])
        ctx.setdefault("themes", [])
        ctx.setdefault("holder_trend", [])
        ctx.setdefault("margin", [])
        ctx.setdefault("lhb", [])
        ctx.setdefault("block_trade", [])
        ctx.setdefault("recent_announcements", [])
        ctx.setdefault("inst_forecast", [])
        return boards
    boards = list(ops.get("ssbk") or [])
    ctx["boards"] = boards
    ctx["themes"] = list(ops.get("hxtc") or [])
    ctx["holder_trend"] = list(ops.get("gdrs") or [])
    ctx["margin"] = list(ops.get("rzrq") or [])
    ctx["lhb"] = list(ops.get("lhbd") or [])
    ctx["block_trade"] = list(ops.get("dzjy") or [])
    ctx["recent_announcements"] = list(ops.get("zxgg") or [])
    ctx["inst_forecast"] = list(ops.get("jgyc") or [])[:6]
    return boards


def _build_compact_stock_context(code: str) -> dict[str, Any]:
    """Fetch compact bundle; independent HTTP calls run in parallel."""
    ctx: dict[str, Any] = {"symbol": code, "secid": stock_secid(code)}
    tasks: dict[str, Callable[[], Any]] = {
        "board_tags": lambda: fetch_stock_board_tags(code),
        "valuation": lambda: fetch_valuation_summary(code),
        "money_flow": lambda: fetch_money_flow_snapshot(code),
        "money_flow_daily": lambda: fetch_money_flow_klines(code, klt="101", lmt=5),
        "money_flow_intraday": lambda: fetch_money_flow_klines(code, klt="1", lmt=5),
        "operations": lambda: fetch_operations_required(code),
        "top_holders": lambda: datacenter_for_symbol("top_holders", code, page_size=3),
        "free_holders": lambda: datacenter_for_symbol("free_holders", code, page_size=3),
        "org_hold": lambda: datacenter_for_symbol("org_hold", code, page_size=3),
        "lhb_buy": lambda: datacenter_for_symbol("lhb_buy", code, page_size=5),
        "lhb_sell": lambda: datacenter_for_symbol("lhb_sell", code, page_size=5),
        "finance_main": lambda: datacenter_for_symbol("finance_main", code, page_size=2),
        "business": lambda: fetch_business_analysis(code),
        "management": lambda: fetch_company_management(code),
        "bonus_financing": lambda: fetch_bonus_financing(code),
        "big_news": lambda: fetch_company_big_news(code),
        "news_headlines": lambda: fetch_stock_news(code, page_size=3),
        "announcements": lambda: fetch_stock_announcements(code, page_size=3),
        "research_reports": lambda: fetch_research_reports(code, page_size=3),
        "portal": lambda: fetch_portal_datacenter_bundle(code),
    }
    list_keys = frozenset(
        {
            "money_flow_daily",
            "money_flow_intraday",
            "top_holders",
            "free_holders",
            "org_hold",
            "lhb_buy",
            "lhb_sell",
            "finance_main",
            "news_headlines",
            "announcements",
            "research_reports",
        }
    )
    with ThreadPoolExecutor(max_workers=_COMPACT_PARALLEL_WORKERS) as pool:
        futures = {pool.submit(fn): key for key, fn in tasks.items()}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                ctx[key] = fut.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning("compact fetch %s failed for %s: %s", key, code, exc)
                ctx[key] = [] if key in list_keys else None
    boards = _apply_operations_fields(ctx, ctx.get("operations"))
    try:
        ctx["board_flows"] = fetch_stock_board_money_flows(code, boards, limit=3)
    except Exception as exc:  # noqa: BLE001
        logger.warning("compact board_flows failed for %s: %s", code, exc)
        ctx["board_flows"] = []
    return ctx


def _fmt_jgyc_eps_pe(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for i in range(1, 5):
        year = row.get(f"YEAR{i}")
        if year in (None, ""):
            continue
        mark = row.get(f"YEAR_MARK{i}") or ""
        eps = row.get(f"EPS{i}")
        pe = row.get(f"PE{i}")
        eps_s = f"{float(eps):.2f}" if eps not in (None, "") else "—"
        pe_s = f"{float(pe):.1f}" if pe not in (None, "") else "—"
        parts.append(f"{year}{mark} EPS{eps_s}/PE{pe_s}")
    return " · ".join(parts)


def _fmt_money_yuan(val: Any) -> str:
    try:
        n = float(val)
    except (TypeError, ValueError):
        return str(val) if val not in (None, "") else "—"
    sign = "+" if n > 0 else ""
    if abs(n) >= 1e8:
        return f"{sign}{n / 1e8:.2f}亿"
    if abs(n) >= 1e4:
        return f"{sign}{n / 1e4:.2f}万"
    return f"{sign}{n:.0f}"


def format_compact_stock_context_for_prompt(ctx: dict[str, Any]) -> str:
    """Render ``fetch_compact_stock_context`` as markdown for LLM prompts."""
    lines: list[str] = [
        "## 东财单股扩展上下文（程序抓取，供阶段一/二参考；不含筹码分布）",
        "",
        f"代码 {ctx.get('symbol', '—')}",
        "",
    ]
    tags = ctx.get("board_tags") or {}
    if tags:
        concepts = tags.get("concepts") or []
        concept_s = "、".join(concepts[:12])
        if len(concepts) > 12:
            concept_s += f" 等{len(concepts)}项"
        lines += [
            "### 板块与概念（stock/get）",
            f"- 行业：{tags.get('industry') or '—'}",
            f"- 地域：{tags.get('region') or '—'}",
            f"- 概念：{concept_s or '—'}",
            "",
        ]
    val = ctx.get("valuation") or {}
    if val:
        lines += [
            "### 估值与市值",
            f"- 现价 {val.get('price', '—')} · 动态PE {val.get('pe_dynamic', '—')} · "
            f"PB {val.get('pb', '—')} · 换手 {val.get('turnover_pct', '—')}%",
            f"- 总市值 {_fmt_money_yuan(val.get('total_mv'))} · "
            f"流通市值 {_fmt_money_yuan(val.get('float_mv'))}",
            "",
        ]
    inst_fc = ctx.get("inst_forecast") or []
    if inst_fc:
        lines.append("### 机构盈利预测（F10 jgyc）")
        shown = 0
        for row in inst_fc:
            if not isinstance(row, dict):
                continue
            org = row.get("ORG_NAME_ABBR") or "—"
            detail = _fmt_jgyc_eps_pe(row)
            if not detail:
                continue
            lines.append(f"- {org}: {detail}")
            shown += 1
            if shown >= 4:
                break
        lines.append("")
    board_flows = ctx.get("board_flows") or []
    if board_flows:
        lines.append("### 所属板块资金流向（当日主力）")
        for bf in board_flows[:3]:
            lines.append(
                f"- {bf.get('board_name', '—')}: "
                f"主力 {_fmt_money_yuan(bf.get('main_net'))} "
                f"（{bf.get('main_net_pct', '—')}%）· 板块涨跌 {bf.get('pct_chg', '—')}%"
            )
        lines.append("")
    mf = ctx.get("money_flow") or {}
    if mf:
        lines += [
            "### 资金流向快照（当日）",
            f"- 主力净流入：{_fmt_money_yuan(mf.get('main_net'))}"
            f"（占比 {mf.get('main_net_pct') or '—'}%）",
            f"- 超大单：{_fmt_money_yuan(mf.get('super_large_net'))} · "
            f"大单：{_fmt_money_yuan(mf.get('large_net'))} · "
            f"中单：{_fmt_money_yuan(mf.get('medium_net'))} · "
            f"小单：{_fmt_money_yuan(mf.get('small_net'))}",
            "",
        ]
    daily = ctx.get("money_flow_daily") or []
    if daily:
        lines.append("### 近5日主力净流入（日K）")
        for row in daily[:5]:
            lines.append(
                f"- {row.get('date', '—')}: {_fmt_money_yuan(row.get('main_net'))}"
                f"（收 {row.get('close', '—')} 涨跌 {row.get('change_pct', '—')}%）"
            )
        lines.append("")
    intraday = ctx.get("money_flow_intraday") or []
    if intraday:
        lines.append("### 当日分时主力净流入（最近）")
        for row in intraday[-3:]:
            lines.append(
                f"- {row.get('time', '—')}: {_fmt_money_yuan(row.get('main_net'))}"
            )
        lines.append("")
    top_h = ctx.get("top_holders") or []
    if top_h:
        lines.append("### 十大股东（最新）")
        for row in top_h[:3]:
            lines.append(
                f"- {row.get('HOLDER_NAME', '—')}: "
                f"持股 {row.get('HOLD_NUM', '—')} · "
                f"占比 {row.get('HOLD_RATIO', '—')}%"
            )
        lines.append("")
    free_h = ctx.get("free_holders") or []
    if free_h:
        lines.append("### 十大流通股东（最新）")
        for row in free_h[:3]:
            lines.append(
                f"- {row.get('HOLDER_NAME', '—')}: "
                f"持股 {row.get('HOLD_NUM', '—')} · "
                f"占比 {row.get('HOLD_RATIO', '—')}%"
            )
        lines.append("")
    org_h = ctx.get("org_hold") or []
    if org_h:
        lines.append("### 机构持股（最新）")
        for row in org_h[:3]:
            lines.append(
                f"- {str(row.get('REPORT_DATE', row.get('END_DATE', '')))[:10]} · "
                f"机构数 {row.get('ORG_NUM', row.get('ORG_QUANTITY', '—'))} · "
                f"持股 {row.get('TOTAL_SHARES', row.get('HOLD_SHARES', '—'))} · "
                f"占流通比 {row.get('TOTAL_SHARES_RATIO', row.get('FREE_SHARES_RATIO', '—'))}%"
            )
        lines.append("")
    mgmt = ctx.get("management") or {}
    gglb = list(mgmt.get("gglb") or [])
    if gglb:
        lines.append("### 公司高管（F10）")
        for row in gglb[:3]:
            if not isinstance(row, dict):
                continue
            name = row.get("PERSON_NAME") or "—"
            pos = row.get("POSITION") or "—"
            hold = row.get("HOLD_NUM")
            hold_s = f" · 持股 {hold}" if hold not in (None, "", "0") else ""
            lines.append(f"- {name}（{pos}）{hold_s}")
        lines.append("")
    fin = ctx.get("finance_main") or []
    if fin:
        f0 = fin[0]
        lines += [
            "### 主要财务（最新报告期）",
            f"- 报告期 {str(f0.get('REPORT_DATE', ''))[:10]} · "
            f"营收 {_fmt_money_yuan(f0.get('TOTAL_OPERATE_INCOME'))} · "
            f"净利 {_fmt_money_yuan(f0.get('PARENT_NETPROFIT'))} · "
            f"ROE {f0.get('WEIGHTAVG_ROE', '—')}%",
            "",
        ]
    bonus = ctx.get("bonus_financing") or {}
    fhyx = list(bonus.get("fhyx") or [])
    if fhyx:
        lines.append("### 分红送转（F10 近年）")
        for row in fhyx[:3]:
            if not isinstance(row, dict):
                continue
            nd = str(row.get("NOTICE_DATE") or "")[:10]
            plan = row.get("IMPL_PLAN_PROFILE") or "—"
            progress = row.get("ASSIGN_PROGRESS") or "—"
            ex_dt = str(row.get("EX_DIVIDEND_DATE") or "")[:10]
            ex_s = f" · 除权 {ex_dt}" if ex_dt and ex_dt != "None" else ""
            lines.append(f"- [{nd}] {progress}: {str(plan)[:100]}{ex_s}")
        lines.append("")
    biz = ctx.get("business") or {}
    zygc = list(biz.get("zygcfx") or [])
    if zygc:
        lines.append("### 主营构成（最新）")
        for row in zygc[:3]:
            if isinstance(row, dict):
                lines.append(
                    f"- {row.get('MAIN_BUSINESS', row.get('PRODUCT_NAME', '—'))}: "
                    f"收入占比 {row.get('MBI_RATIO', row.get('INCOME_RATIO', '—'))}%"
                )
            else:
                lines.append(f"- {str(row)[:120]}")
        lines.append("")
    bnews = ctx.get("big_news") or {}
    dstx = list(bnews.get("dstx") or [])
    if dstx:
        lines.append("### 公司重大事项（F10）")
        for row in dstx[:3]:
            if isinstance(row, dict):
                lines.append(
                    f"- {str(row.get('NOTICE_DATE', row.get('TRADE_DATE', '')))[:10]} · "
                    f"{row.get('EVENT_TYPE', row.get('TITLE', '—'))}"
                )
            else:
                lines.append(f"- {str(row)[:120]}")
        lines.append("")
    news = ctx.get("news_headlines") or []
    if news:
        lines.append("### 近期新闻")
        for row in news[:3]:
            title = row.get("title") or row.get("Title") or "—"
            nd = str(row.get("showTime") or row.get("datetime") or "")[:16]
            lines.append(f"- [{nd}] {title[:80]}")
        lines.append("")
    holders = ctx.get("holder_trend") or []
    if holders:
        h0 = holders[0]
        lines += [
            "### 股东户数（最新）",
            f"- 报告期 {str(h0.get('END_DATE', ''))[:10]} · "
            f"户数 {h0.get('HOLDER_TOTAL_NUM', '—')} · "
            f"环比 {h0.get('TOTAL_NUM_RATIO', '—')}% · "
            f"人均持股 {h0.get('AVG_FREE_SHARES', '—')} · "
            f"集中度 {h0.get('HOLD_FOCUS', '—')}",
            "",
        ]
    margin = ctx.get("margin") or []
    if margin:
        m0 = margin[0]
        lines += [
            "### 融资融券（最新）",
            f"- 日期 {str(m0.get('TRADE_DATE', ''))[:10]} · "
            f"融资余额 {_fmt_money_yuan(m0.get('FIN_BALANCE'))} · "
            f"融券余额 {_fmt_money_yuan(m0.get('LOAN_BALANCE'))}",
            "",
        ]
    lhb = ctx.get("lhb") or []
    if lhb:
        l0 = lhb[0]
        lines += [
            "### 龙虎榜（最近）",
            f"- {str(l0.get('TRADE_DATE', ''))[:10]} · {l0.get('EXPLANATION', '—')} · "
            f"买入合计 {_fmt_money_yuan(l0.get('TOTAL_BUY'))} · "
            f"卖出合计 {_fmt_money_yuan(l0.get('TOTAL_SELL'))}",
            "",
        ]
    lhb_buy = ctx.get("lhb_buy") or []
    if lhb_buy:
        lines.append("### 龙虎榜买入席位（数据中心）")
        for row in lhb_buy[:3]:
            lines.append(
                f"- {str(row.get('TRADE_DATE', ''))[:10]} · "
                f"{row.get('OPERATEDEPT_NAME', '—')}: "
                f"买入 {_fmt_money_yuan(row.get('BUY'))}"
            )
        lines.append("")
    lhb_sell = ctx.get("lhb_sell") or []
    if lhb_sell:
        lines.append("### 龙虎榜卖出席位（数据中心）")
        for row in lhb_sell[:3]:
            lines.append(
                f"- {str(row.get('TRADE_DATE', ''))[:10]} · "
                f"{row.get('OPERATEDEPT_NAME', '—')}: "
                f"卖出 {_fmt_money_yuan(row.get('SELL'))}"
            )
        lines.append("")
    dzjy = ctx.get("block_trade") or []
    if dzjy:
        d0 = dzjy[0]
        lines += [
            "### 大宗交易（最近）",
            f"- {str(d0.get('TRADE_DATE', ''))[:10]} · "
            f"成交价 {d0.get('DEAL_PRICE', '—')} · "
            f"溢价率 {d0.get('PREMIUM_RATIO', '—')}% · "
            f"成交额 {_fmt_money_yuan(d0.get('DEAL_AMT'))}",
            "",
        ]
    boards = ctx.get("boards") or []
    if boards:
        names = [b.get("BOARD_NAME", "") for b in boards[:10] if b.get("BOARD_NAME")]
        if names:
            lines += ["### 所属板块（F10 ssbk）", f"- {'、'.join(names)}", ""]
    themes = ctx.get("themes") or []
    if themes:
        kw = [t.get("KEYWORD", "") for t in themes[:6] if t.get("KEYWORD")]
        if kw:
            lines += ["### 核心题材要点", f"- {'、'.join(kw)}", ""]
    ann = ctx.get("recent_announcements") or []
    if ann:
        lines.append("### 近期公告（F10）")
        for a in ann[:3]:
            title = a.get("title") or a.get("title_ch") or "—"
            nd = str(a.get("notice_date") or a.get("display_time") or "")[:10]
            lines.append(f"- [{nd}] {title}")
        lines.append("")
    api_ann = ctx.get("announcements") or []
    if api_ann:
        lines.append("### 近期公告（API）")
        for a in api_ann[:3]:
            title = a.get("title") or a.get("title_ch") or "—"
            nd = str(a.get("notice_date") or a.get("display_time") or "")[:10]
            lines.append(f"- [{nd}] {title[:80]}")
        lines.append("")
    reports = ctx.get("research_reports") or []
    if reports:
        lines.append("### 近期研报")
        for row in reports[:3]:
            title = row.get("title") or "—"
            nd = str(row.get("publish_time") or "")[:10]
            rating = row.get("em_rating_name") or row.get("s_rating_name") or "—"
            source = row.get("source") or "—"
            lines.append(f"- [{nd}] {source} · {rating} · {title[:80]}")
        lines.append("")
    portal = ctx.get("portal") or {}
    comment_rows = portal.get("stock_comment") or []
    if comment_rows:
        txt = comment_rows[0].get("COMMENT_TXT") or ""
        if txt:
            lines += ["### 千股千评（东财数据中心）", f"- {txt[:400]}", ""]
    eval_rows = portal.get("stock_evaluate") or []
    if eval_rows:
        e0 = eval_rows[0]
        lines += [
            "### 技术/资金评分（千股千评）",
            f"- 日期 {str(e0.get('TRADE_DATE', ''))[:10]} · "
            f"涨跌 {e0.get('CHANGE_RATE', '—')}% · "
            f"主力净流入 {_fmt_money_yuan(e0.get('PRIME_INFLOW'))} · "
            f"动态PE {e0.get('PE_DYNAMIC', '—')} · "
            f"换手 {e0.get('TURNOVERRATE', '—')}%",
            "",
        ]
    part_rows = portal.get("participation") or []
    if part_rows:
        p0 = part_rows[0]
        lines += [
            "### 参与度",
            f"- 日期 {str(p0.get('TRADE_DATE', ''))[:10]} · "
            f"意愿 {p0.get('PARTICIPATION_WISH', '—')} · "
            f"5日均 {p0.get('PARTICIPATION_WISH_5DAYS', '—')} · "
            f"变化 {p0.get('PARTICIPATION_WISH_CHANGE', '—')}%",
            "",
        ]
    focus_rows = portal.get("market_focus") or []
    if focus_rows:
        f0 = focus_rows[0]
        lines += [
            "### 市场关注度",
            f"- 日期 {str(f0.get('TRADE_DATE', ''))[:10]} · "
            f"关注度 {f0.get('MARKET_FOCUS', '—')} · "
            f"排名 {f0.get('MARKET_FOCUS_RANK', '—')}/{f0.get('TOTAL_MARKET', '—')}",
            "",
        ]
    pledge_rows = portal.get("pledge_summary") or []
    if pledge_rows:
        p0 = pledge_rows[0]
        lines += [
            "### 股权质押（概况）",
            f"- 日期 {str(p0.get('TRADE_DATE', ''))[:10]} · "
            f"质押比例 {p0.get('PLEDGE_RATIO', '—')}% · "
            f"回购余额 {p0.get('REPURCHASE_BALANCE', '—')}亿",
            "",
        ]
    mutual_rows = portal.get("mutual_hold") or []
    if mutual_rows:
        m0 = mutual_rows[0]
        lines += [
            "### 沪深港通持股",
            f"- 日期 {str(m0.get('HOLD_DATE', m0.get('TRADE_DATE', '')))[:10]} · "
            f"持股 {m0.get('HOLD_SHARES', '—')} · "
            f"占流通比 {m0.get('FREE_SHARES_RATIO', m0.get('HOLD_SHARES_RATIO', '—'))}% · "
            f"机构数 {m0.get('ORG_QUANTITY', '—')}",
            "",
        ]
    survey_rows = portal.get("org_survey") or []
    if survey_rows:
        s0 = survey_rows[0]
        lines += [
            "### 机构调研（最近）",
            f"- {str(s0.get('NOTICE_DATE', ''))[:10]} · "
            f"{s0.get('ORG_NAME', s0.get('RECEIVE_OBJECT', '—'))} · "
            f"接待 {s0.get('RECEIVE_WAY', '—')}",
            "",
        ]
    trade_rows = portal.get("holder_trade") or []
    if trade_rows:
        t0 = trade_rows[0]
        lines += [
            "### 股东增减持（最近）",
            f"- {str(t0.get('NOTICE_DATE', ''))[:10]} · "
            f"{t0.get('HOLDER_NAME', '—')} · "
            f"变动 {t0.get('CHANGE_NUM', '—')} · "
            f"占比 {t0.get('CHANGE_RATE', '—')}%",
            "",
        ]
    margin_trend = portal.get("margin_trend") or []
    if margin_trend:
        mt0 = margin_trend[0]
        lines += [
            "### 融资融券趋势",
            f"- {str(mt0.get('TRADE_DATE', ''))[:10]} · "
            f"融资余额 {_fmt_money_yuan(mt0.get('FIN_BALANCE'))} · "
            f"融券余额 {_fmt_money_yuan(mt0.get('LOAN_BALANCE'))} · "
            f"融资变动 {_fmt_money_yuan(mt0.get('FIN_BALANCE_DIFF'))}",
            "",
        ]
    fin_ana = portal.get("fin_analysis") or []
    if fin_ana:
        fa0 = fin_ana[0]
        lines += [
            "### 财务分析指标",
            f"- 报告期 {str(fa0.get('REPORT_DATE', ''))[:10]} · "
            f"ROE {fa0.get('WEIGHT_ROE', '—')}% · "
            f"净利同比 {fa0.get('NETPROFIT_YOY_RATIO', '—')}% · "
            f"资产负债率 {fa0.get('DEBT_ASSET_RATIO', '—')}%",
            "",
        ]
    mutual_deal = portal.get("mutual_deal") or []
    if mutual_deal:
        md0 = mutual_deal[0]
        lines += [
            "### 沪深港通成交（十大）",
            f"- {str(md0.get('TRADE_DATE', ''))[:10]} · "
            f"净买入 {_fmt_money_yuan(md0.get('NET_BUY_AMT'))} · "
            f"排名 {md0.get('RANK', '—')}",
            "",
        ]
    if len(lines) <= 4:
        return ""
    return "\n".join(lines).rstrip()


def format_compact_stock_context_sections(
    ctx: dict[str, Any],
) -> list[tuple[str, str]]:
    """Split compact markdown into GUI-friendly (title, body) sections."""
    text = format_compact_stock_context_for_prompt(ctx)
    if not text:
        return []
    sections: list[tuple[str, str]] = []
    title = ""
    body_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("### "):
            if body_lines:
                body = "\n".join(body_lines).strip()
                if body:
                    sections.append((title or "详情", body))
            title = line[4:].strip()
            body_lines = []
            continue
        if line.startswith("## "):
            continue
        body_lines.append(line)
    if body_lines:
        body = "\n".join(body_lines).strip()
        if body:
            sections.append((title or "详情", body))
    return sections


def fetch_stock_extended_profile(
    symbol: str,
    *,
    include_news: bool = True,
    include_reports: bool = True,
    include_fflow_klines: bool = True,
    list_page_size: int = 10,
) -> dict[str, Any]:
    """Aggregate all currently wired single-stock extended data."""
    code = _symbol_code(symbol)
    profile: dict[str, Any] = {"symbol": code, "secid": stock_secid(code)}
    profile["money_flow"] = fetch_money_flow_snapshot(code)
    profile["valuation_fields"] = fetch_valuation_fields(code)
    profile["company_survey"] = fetch_company_survey(code)
    profile["core_conception"] = fetch_core_conception(code)
    profile["operations_required"] = fetch_operations_required(code)
    profile["capital_structure"] = fetch_capital_stock_structure(code)
    profile["bonus_financing"] = fetch_bonus_financing(code)
    profile["board_tags"] = fetch_stock_board_tags(code)
    profile["shareholder_research"] = fetch_shareholder_research(code)
    profile["business_analysis"] = fetch_business_analysis(code)
    profile["company_management"] = fetch_company_management(code)
    profile["company_big_news"] = fetch_company_big_news(code)
    profile["valuation_summary"] = fetch_valuation_summary(code)
    ops = profile.get("operations_required") or {}
    profile["board_money_flows"] = fetch_stock_board_money_flows(
        code, list(ops.get("ssbk") or []), limit=5
    )
    profile["valuation"] = datacenter_for_symbol("valuation", code, page_size=3)
    profile["finance_main"] = datacenter_for_symbol("finance_main", code, page_size=4)
    profile["industry"] = datacenter_for_symbol("industry", code, page_size=1)
    profile["holder_num"] = datacenter_for_symbol("holder_num", code, page_size=8)
    profile["margin"] = datacenter_for_symbol("margin", code, page_size=10)
    profile["top_holders"] = datacenter_for_symbol("top_holders", code, page_size=10)
    profile["free_holders"] = datacenter_for_symbol("free_holders", code, page_size=10)
    profile["org_hold"] = datacenter_for_symbol("org_hold", code, page_size=5)
    profile["lhb_buy"] = datacenter_for_symbol("lhb_buy", code, page_size=5)
    profile["lhb_sell"] = datacenter_for_symbol("lhb_sell", code, page_size=5)
    profile["block_trade"] = datacenter_for_symbol("block_trade", code, page_size=5)
    profile["portal"] = fetch_portal_datacenter_bundle(code)
    profile["announcements"] = fetch_stock_announcements(
        code, page_size=list_page_size
    )
    if include_fflow_klines:
        profile["money_flow_intraday"] = fetch_money_flow_klines(
            code, klt="1", lmt=0
        )
        profile["money_flow_daily"] = fetch_money_flow_klines(
            code, klt="101", lmt=60
        )
    if include_news:
        profile["news"] = fetch_stock_news(code, page_size=list_page_size)
    if include_reports:
        profile["research_reports"] = fetch_research_reports(
            code, page_size=list_page_size
        )
    return profile
