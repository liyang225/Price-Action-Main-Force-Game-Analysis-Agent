"""User-facing Chinese messages for TradingView / tvdatafeed failures."""
from __future__ import annotations

from pa_agent.data.market_defaults import (
    TV_HK_EXCHANGE,
    infer_ashare_tv_exchange,
    is_ashare_tv_request,
    is_hk_tv_request,
    normalize_ashare_tv_code,
    normalize_hk_tv_code,
)
from pa_agent.data.tv_symbol_lookup import is_tv_name_input


def format_tradingview_fetch_error(
    symbol: str,
    exchange: str,
    *,
    empty_data: bool = False,
    cause: BaseException | None = None,
) -> str:
    """Return a short status-bar message for a failed TV snapshot."""
    sym = (symbol or "").strip()
    ex = (exchange or "").strip().upper()
    ex_hint = ex if ex else "未填写"

    cause_text = str(cause or "").lower()
    if "timed out" in cause_text or "timeout" in cause_text:
        if is_ashare_tv_request(ex, sym):
            code = normalize_ashare_tv_code(sym)
            inferred = infer_ashare_tv_exchange(code)
            extra = ""
            if code.startswith(("688", "689")) and ex == "SZSE":
                extra = "（科创板 688/689 只能用 SSE，不能用 SZSE）"
            return (
                f"TradingView 连接超时（{ex_hint} / {sym}）{extra}："
                f"请检查能否访问 TradingView，A 股建议 {inferred}+{code}，稍后重试"
            )
        return (
            f"TradingView 连接超时（{ex_hint} / {sym}）："
            "请检查网络能否访问 TradingView，或确认交易所 OANDA + 品种 XAUUSD"
        )

    if sym.lower().endswith("m") and len(sym) > 2:
        return (
            f"TradingView 无数据（{ex_hint} / {sym}）："
            "品种名像 MT5 券商后缀（…m），请去掉 m 或改用「数据来源 → MT5」。"
            "黄金示例：交易所 OANDA + 品种 XAUUSD"
        )

    if ex == "TVC" and sym.upper() == "XAUUSD":
        return (
            "TradingView 组合错误：TVC 上黄金是 GOLD，不是 XAUUSD。"
            "请用 OANDA + XAUUSD，或 TVC + GOLD"
        )

    if empty_data or "no data" in cause_text:
        if is_ashare_tv_request(ex, sym):
            code = normalize_ashare_tv_code(sym)
            inferred = infer_ashare_tv_exchange(code)
            star_hint = ""
            if code.startswith(("688", "689")):
                star_hint = "科创板 688/689 在 TradingView 上属于上交所 SSE，"
                if ex == "SZSE":
                    star_hint += "SZSE 无此标的；"
            return (
                f"TradingView 无 A 股数据（{ex_hint} / {sym}）："
                f"{star_hint}请用 {inferred} + {code}（例 SSE:600519、SZSE:000001）"
            )
        if is_hk_tv_request(ex, sym):
            code = normalize_hk_tv_code(sym)
            return (
                f"TradingView 无港股数据（{ex_hint} / {sym}）："
                f"请用 {TV_HK_EXCHANGE} + {code}（勿加前导零，如 1810 非 01810）；"
                "或输入已支持名称如「小米集团」"
            )
        if not ex:
            return (
                f"TradingView 无数据（品种 {sym}，未填交易所）："
                "黄金 OANDA+XAUUSD；A 股 SSE/SZSE+6 位；港股 HKEX+代码或名称"
            )
        if is_tv_name_input(sym):
            return (
                f"TradingView 无数据（名称 {sym}）："
                "请改用代码，或在 config/tv_symbol_aliases.json 添加别名"
            )
        hint = "OANDA + XAUUSD"
        if ex == "TVC":
            hint = "TVC + GOLD"
        elif ex == "CAPITALCOM":
            hint = "CAPITALCOM + GOLD"
        return (
            f"TradingView 无数据（{ex} / {sym}）："
            f"该组合可能无效，现货黄金请用 {hint}"
        )

    if cause is not None:
        return f"TradingView 拉取失败（{ex_hint} / {sym}）：{cause}"

    return f"TradingView 拉取失败（{ex_hint} / {sym}），请检查交易所与品种"
