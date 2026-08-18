"""Resolve TradingView exchange/symbol from Chinese or English names."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pa_agent.config.paths import CONFIG_DIR

logger = logging.getLogger(__name__)

# (exchange, symbol) — verified via tvDatafeed get_hist where noted
_BUILTIN_ALIASES: dict[str, tuple[str, str]] = {
    # ── 港股 HKEX（代码勿加前导零，如 700 而非 0700）────────────────────────
    "小米": ("HKEX", "1810"),
    "小米集团": ("HKEX", "1810"),
    "xiaomi": ("HKEX", "1810"),
    "腾讯": ("HKEX", "700"),
    "腾讯控股": ("HKEX", "700"),
    "tencent": ("HKEX", "700"),
    "美团": ("HKEX", "3690"),
    "美团-w": ("HKEX", "3690"),
    "meituan": ("HKEX", "3690"),
    "阿里巴巴": ("HKEX", "9988"),
    "阿里巴巴-w": ("HKEX", "9988"),
    "阿里": ("HKEX", "9988"),
    "alibaba": ("HKEX", "9988"),
    "比亚迪": ("HKEX", "1211"),
    "byd": ("HKEX", "1211"),
    "中国移动": ("HKEX", "941"),
    "建设银行": ("HKEX", "939"),
    "工商银行": ("HKEX", "1398"),
    "汇丰控股": ("HKEX", "5"),
    "hsbc": ("HKEX", "5"),
    "友邦保险": ("HKEX", "1299"),
    "aia": ("HKEX", "1299"),
    "快手": ("HKEX", "1024"),
    "kuaishou": ("HKEX", "1024"),
    "京东": ("HKEX", "9618"),
    "jd": ("HKEX", "9618"),
    "网易": ("HKEX", "9999"),
    "netease": ("HKEX", "9999"),
    "百度": ("HKEX", "9888"),
    "baidu": ("HKEX", "9888"),
    "李宁": ("HKEX", "2331"),
    "哔哩哔哩": ("HKEX", "9626"),
    "哔哩哔哩-w": ("HKEX", "9626"),
    "b站": ("HKEX", "9626"),
    "bilibili": ("HKEX", "9626"),
    "bili": ("NASDAQ", "BILI"),
    "理想汽车": ("HKEX", "2015"),
    "蔚来": ("HKEX", "9866"),
    "nio": ("HKEX", "9866"),
    "小鹏汽车": ("HKEX", "9868"),
    "xpeng": ("HKEX", "9868"),
    # ── A 股（6 位代码 + SSE/SZSE）──────────────────────────────────────────
    "贵州茅台": ("SSE", "600519"),
    "茅台": ("SSE", "600519"),
    "宁德时代": ("SZSE", "300750"),
    "平安银行": ("SZSE", "000001"),
    "中国平安": ("SSE", "601318"),
    "招商银行": ("SSE", "600036"),
    "工商银行a": ("SSE", "601398"),
    "比亚迪a": ("SZSE", "002594"),
    "中芯国际": ("SSE", "688981"),
    "紫金矿业": ("SSE", "601899"),
    "沪深300": ("SSE", "000300"),
}

_NAME_SUFFIXES = (
    "集团股份有限公司",
    "股份有限公司",
    "有限公司",
    "控股集团",
    "集团",
    "股份",
    "控股",
)

_aliases_cache: dict[str, tuple[str, str]] | None = None


class TvSymbolNotFoundError(ValueError):
    """Raised when a name cannot be mapped to a TradingView symbol."""


def _normalize_name_key(name: str) -> str:
    s = (name or "").strip().lower()
    for suf in _NAME_SUFFIXES:
        if s.endswith(suf.lower()):
            s = s[: -len(suf)]
    s = re.sub(r"\s+", "", s)
    return s


def _load_user_aliases() -> dict[str, tuple[str, str]]:
    path = CONFIG_DIR / "tv_symbol_aliases.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return {}
    out: dict[str, tuple[str, str]] = {}
    if isinstance(raw, dict):
        for key, val in raw.items():
            if isinstance(val, (list, tuple)) and len(val) >= 2:
                out[_normalize_name_key(str(key))] = (
                    str(val[0]).strip().upper(),
                    str(val[1]).strip(),
                )
    return out


def _all_aliases() -> dict[str, tuple[str, str]]:
    global _aliases_cache
    if _aliases_cache is None:
        merged = dict(_BUILTIN_ALIASES)
        merged.update(_load_user_aliases())
        _aliases_cache = {
            _normalize_name_key(k): v for k, v in merged.items()
        }
    return _aliases_cache


def reload_tv_symbol_aliases() -> None:
    """Clear cache after user edits ``config/tv_symbol_aliases.json``."""
    global _aliases_cache
    _aliases_cache = None


def is_tv_name_input(symbol: str) -> bool:
    """True when input looks like a name rather than a numeric/gold ticker."""
    s = (symbol or "").strip()
    if not s or s.isdigit():
        return False
    upper = s.upper()
    if upper in ("XAUUSD", "GOLD", "XAGUSD", "EURUSD", "GBPUSD"):
        return False
    # Only all-lowercase English is likely a name (e.g. "xiaomi");
    # uppercase / mixed-case short strings are ticker symbols (e.g. "SPX", "NDX").
    if re.fullmatch(r"[a-z0-9.\-]+", s) and any(c.isalpha() for c in s):
        return len(s) >= 3
    if re.search(r"[\u4e00-\u9fff]", s):
        return True
    return False


def lookup_tv_symbol_by_name(name: str) -> tuple[str, str] | None:
    """Return ``(exchange, symbol)`` for a company name, or None."""
    key = _normalize_name_key(name)
    if not key:
        return None
    aliases = _all_aliases()
    if key in aliases:
        return aliases[key]
    for alias_key, pair in aliases.items():
        if key in alias_key or alias_key in key:
            if len(key) >= 2 and len(alias_key) >= 2:
                return pair
    return None


def resolve_tv_symbol_name(name: str) -> tuple[str, str]:
    """Resolve name to ``(exchange, symbol)``; raise if unknown."""
    hit = lookup_tv_symbol_by_name(name)
    if hit is None:
        raise TvSymbolNotFoundError(
            f"未找到股票名称「{name.strip()}」。"
            "请改用代码（港股 HKEX+1810、A 股 6 位），"
            "或在 config/tv_symbol_aliases.json 中添加别名。"
        )
    return hit
