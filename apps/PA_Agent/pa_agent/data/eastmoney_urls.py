"""Build quote.eastmoney.com URLs for embedded web charts."""
from __future__ import annotations

from pa_agent.data.ashare_common import is_index_symbol, normalize_ashare_symbol

# PA timeframe → East Money kline type hint (for future deep-link; page uses in-page tabs)
_TF_KLT: dict[str, str] = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "4h": "60",
    "1d": "101",
    "1w": "102",
    "1M": "103",
}


def _market_prefix(symbol: str) -> tuple[str, str]:
    """Return (url_prefix, six_digit_code) e.g. ('sh', '600519')."""
    sym = normalize_ashare_symbol(symbol)
    if not sym:
        return "sh", "000001"
    if sym.startswith(("sh", "sz")) and len(sym) >= 8:
        return sym[:2].lower(), sym[2:8]
    digits = sym[-6:] if len(sym) >= 6 else sym.zfill(6)
    if is_index_symbol(sym):
        return "zs", digits
    if digits[0] in ("5", "6", "9"):
        return "sh", digits
    return "sz", digits


def quote_page_url(symbol: str, *, timeframe: str = "1d") -> str:
    """Full East Money quote page (same UI as opening in Chrome)."""
    prefix, code = _market_prefix(symbol)
    base = f"https://quote.eastmoney.com/{prefix}{code}.html"
    klt = _TF_KLT.get(timeframe, "101")
    return f"{base}?klt={klt}"


def quote_page_url_simple(symbol: str) -> str:
    prefix, code = _market_prefix(symbol)
    return f"https://quote.eastmoney.com/{prefix}{code}.html"
