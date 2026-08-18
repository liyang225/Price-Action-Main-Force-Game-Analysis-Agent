"""东方财富期货 K 线数据源 (基于 AkShare 国内期货接口).

通过 AkShare 获取国内期货主力合约的 OHLCV 数据：
- 主力合约列表: ``futures_display_main_sina`` (82 个主力连续合约)
- 日 K 线: ``futures_zh_daily_sina(symbol)`` (列: date/open/high/low/close/volume)
- 分钟 K 线: ``futures_zh_minute_sina(symbol, period)`` (period=1/5/15/30/60)

主力合约代码格式: 字母+0, 例如 RB0(螺纹钢)、AU0(黄金)、CU0(铜)、FU0(燃油)。
AkShare 期货数据底层来自新浪财经，统一封装在国内期货接口中。
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pa_agent.data.base import DataSource, DataSourceTransientError, KlineBar, normalize_kline_bar
from pa_agent.data.akshare_source import (
    _df_to_bars_asc as _ashare_df_to_bars_asc,
    _merge_ohlcv,
    _normalize_ohlcv_df,
    _resample_rows_to_4h,
    _rows_to_kline_bars,
)

logger = logging.getLogger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")

# AkShare 期货接口限流: 拉长请求间隔避免被限。
_AK_MIN_INTERVAL_S = 0.9
_last_ak_fetch_mono: float = 0.0

# PA Agent timeframe -> AkShare 期货分钟 period (字符串)
_TF_MINUTE_PERIOD: dict[str, str] = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
}

# 通用重采样: 将分钟线按 chunk_size 根合并为一根 (如 chunk_size=2 → 2h from 1h)
def _resample_rows(rows_asc: list[dict[str, Any]], chunk_size: int) -> list[dict[str, Any]]:
    if not rows_asc or chunk_size <= 1:
        return list(rows_asc)
    buckets: list[dict[str, Any]] = []
    chunk: list[dict[str, Any]] = []
    for row in rows_asc:
        chunk.append(row)
        if len(chunk) == chunk_size:
            buckets.append(_merge_ohlcv(chunk))
            chunk = []
    if chunk:
        buckets.append(_merge_ohlcv(chunk))
    return buckets

# 支持的周期: 分钟线(1/5/15/30/60m) + 2h/4h(由1h重采样) + 日线
_SUPPORTED_TIMEFRAMES: tuple[str, ...] = (
    "1m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "1d",
)

# 品种代码 -> 中文名称 (下拉框显示 "代码 中文名", subscribe 时自动提取代码)
# 用户也可输入任意主力代码 (如 RB0/AU0/CU0) 或具体月份 (如 AO2501)
_SYMBOL_NAMES: dict[str, str] = {
    "V0": "PVC", "P0": "棕榈油", "B0": "豆二", "M0": "豆粕", "I0": "铁矿石",
    "JD0": "鸡蛋", "L0": "塑料", "PP0": "聚丙烯", "FB0": "纤维板", "Y0": "豆油",
    "C0": "玉米", "A0": "豆一", "J0": "焦炭", "JM0": "焦煤", "CS0": "淀粉",
    "EG0": "乙二醇", "RR0": "粳米", "EB0": "苯乙烯", "PG0": "液化石油气", "LH0": "生猪",
    "LG0": "原木", "BZ0": "纯苯",
    "TA0": "PTA", "OI0": "菜油", "RS0": "菜籽", "RM0": "菜粕", "WH0": "强麦",
    "JR0": "粳稻", "SR0": "白糖", "CF0": "棉花", "RI0": "早籼稻", "MA0": "甲醇",
    "FG0": "玻璃", "LR0": "晚籼稻", "SF0": "硅铁", "SM0": "锰硅", "CY0": "棉纱",
    "AP0": "苹果", "CJ0": "红枣", "UR0": "尿素", "SA0": "纯碱", "PF0": "短纤",
    "PK0": "花生", "SH0": "烧碱", "PX0": "对二甲苯", "PR0": "瓶片", "PL0": "丙烯",
    "FU0": "燃料油", "SC0": "原油", "AL0": "铝", "RU0": "天然橡胶", "ZN0": "沪锌",
    "CU0": "铜", "AU0": "黄金", "RB0": "螺纹钢", "PB0": "铅", "AG0": "白银",
    "BU0": "沥青", "HC0": "热轧卷板", "SN0": "锡", "NI0": "镍", "SP0": "纸浆",
    "NR0": "20号胶", "SS0": "不锈钢", "LU0": "低硫燃料油", "BC0": "国际铜", "AO0": "氧化铝",
    "BR0": "丁二烯橡胶", "EC0": "集运欧线", "AD0": "铸造铝合金", "OP0": "胶版印刷纸",
    "IF0": "沪深300股指", "TF0": "5年期国债", "IH0": "上证50股指", "IC0": "中证500股指",
    "TS0": "2年期国债", "IM0": "中证1000股指",
    "SI0": "工业硅", "LC0": "碳酸锂", "PS0": "多晶硅", "PT0": "铂", "PD0": "钯",
}

# 预设主力合约列表 (从 _SYMBOL_NAMES 生成)
_PRESET_SYMBOLS: tuple[str, ...] = tuple(_SYMBOL_NAMES.keys())

# 品种元信息: 品种前缀(去0) -> {交易所, 合约月份规则, 夜盘时段}
# 用于标注每个品种有哪些具体月份合约、交易时段等
_VARIETY_INFO: dict[str, dict[str, str]] = {
    "V": {"exchange": "DCE", "months": "1-12月", "night": "21:00-23:00"},
    "P": {"exchange": "DCE", "months": "1-12月", "night": "21:00-23:00"},
    "B": {"exchange": "DCE", "months": "1-12月", "night": "21:00-23:00"},
    "M": {"exchange": "DCE", "months": "1,3,5,7,8,9,11,12月", "night": "21:00-23:00"},
    "I": {"exchange": "DCE", "months": "1-12月", "night": "21:00-23:00"},
    "JD": {"exchange": "DCE", "months": "1-12月", "night": "无夜盘"},
    "L": {"exchange": "DCE", "months": "1-12月", "night": "21:00-23:00"},
    "PP": {"exchange": "DCE", "months": "1-12月", "night": "21:00-23:00"},
    "FB": {"exchange": "DCE", "months": "1-12月", "night": "无夜盘"},
    "Y": {"exchange": "DCE", "months": "1,3,5,7,8,9,11,12月", "night": "21:00-23:00"},
    "C": {"exchange": "DCE", "months": "1,3,5,7,9,11月(单月)", "night": "21:00-23:00"},
    "A": {"exchange": "DCE", "months": "1-12月", "night": "21:00-23:00"},
    "J": {"exchange": "DCE", "months": "1-12月", "night": "21:00-23:00"},
    "JM": {"exchange": "DCE", "months": "1-12月", "night": "21:00-23:00"},
    "CS": {"exchange": "DCE", "months": "1,3,5,7,9,11月(单月)", "night": "21:00-23:00"},
    "EG": {"exchange": "DCE", "months": "1-12月", "night": "21:00-23:00"},
    "RR": {"exchange": "DCE", "months": "1-12月", "night": "无夜盘"},
    "EB": {"exchange": "DCE", "months": "1-12月", "night": "21:00-23:00"},
    "PG": {"exchange": "DCE", "months": "1-12月", "night": "21:00-23:00"},
    "LH": {"exchange": "DCE", "months": "1-12月", "night": "无夜盘"},
    "LG": {"exchange": "DCE", "months": "1-12月", "night": "无夜盘"},
    "BZ": {"exchange": "DCE", "months": "1-12月", "night": "21:00-23:00"},
    "TA": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "OI": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "RS": {"exchange": "CZCE", "months": "1-12月", "night": "无夜盘"},
    "RM": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "WH": {"exchange": "CZCE", "months": "1-12月", "night": "无夜盘"},
    "JR": {"exchange": "CZCE", "months": "1-12月", "night": "无夜盘"},
    "SR": {"exchange": "CZCE", "months": "1,3,5,7,9,11月(单月)", "night": "21:00-23:00"},
    "CF": {"exchange": "CZCE", "months": "1,3,5,7,9,11月(单月)", "night": "21:00-23:00"},
    "RI": {"exchange": "CZCE", "months": "1-12月", "night": "无夜盘"},
    "MA": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "FG": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "LR": {"exchange": "CZCE", "months": "1-12月", "night": "无夜盘"},
    "SF": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "SM": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "CY": {"exchange": "CZCE", "months": "1-12月", "night": "无夜盘"},
    "AP": {"exchange": "CZCE", "months": "1,3,5,7,10,11,12月", "night": "21:00-23:00"},
    "CJ": {"exchange": "CZCE", "months": "1,3,5,7,9,12月", "night": "21:00-23:00"},
    "UR": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "SA": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "PF": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "PK": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "SH": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "PX": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "PR": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "PL": {"exchange": "CZCE", "months": "1-12月", "night": "21:00-23:00"},
    "FU": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-23:00"},
    "SC": {"exchange": "INE", "months": "1-12月", "night": "21:00-02:30"},
    "AL": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-01:00"},
    "RU": {"exchange": "SHFE", "months": "1-12月(除2/12月)", "night": "21:00-23:00"},
    "ZN": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-01:00"},
    "CU": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-01:00"},
    "AU": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-02:30"},
    "RB": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-23:00"},
    "PB": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-01:00"},
    "AG": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-02:30"},
    "BU": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-23:00"},
    "HC": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-23:00"},
    "SN": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-01:00"},
    "NI": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-01:00"},
    "SP": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-23:00"},
    "NR": {"exchange": "INE", "months": "1-12月", "night": "21:00-23:00"},
    "SS": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-01:00"},
    "LU": {"exchange": "INE", "months": "1-12月", "night": "21:00-23:00"},
    "BC": {"exchange": "INE", "months": "1-12月", "night": "21:00-23:00"},
    "AO": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-01:00"},
    "BR": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-23:00"},
    "EC": {"exchange": "INE", "months": "1-12月", "night": "21:00-23:00"},
    "AD": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-01:00"},
    "OP": {"exchange": "SHFE", "months": "1-12月", "night": "21:00-23:00"},
    "IF": {"exchange": "CFFEX", "months": "当月/下月/随后两个季月", "night": "无夜盘"},
    "TF": {"exchange": "CFFEX", "months": "最近三个季月(3/6/9/12)", "night": "无夜盘"},
    "IH": {"exchange": "CFFEX", "months": "当月/下月/随后两个季月", "night": "无夜盘"},
    "IC": {"exchange": "CFFEX", "months": "当月/下月/随后两个季月", "night": "无夜盘"},
    "TS": {"exchange": "CFFEX", "months": "最近三个季月(3/6/9/12)", "night": "无夜盘"},
    "IM": {"exchange": "CFFEX", "months": "当月/下月/随后两个季月", "night": "无夜盘"},
    "SI": {"exchange": "GFEX", "months": "1-12月", "night": "无夜盘"},
    "LC": {"exchange": "GFEX", "months": "1-12月", "night": "无夜盘"},
    "PS": {"exchange": "GFEX", "months": "1-12月", "night": "无夜盘"},
    "PT": {"exchange": "GFEX", "months": "1-12月", "night": "无夜盘"},
    "PD": {"exchange": "GFEX", "months": "1-12月", "night": "无夜盘"},
}


def variety_prefix(main_code: str) -> str:
    """从主力代码提取品种前缀 (如 'RB0' -> 'RB', 'AO0' -> 'AO')."""
    m = re.match(r"^([A-Za-z]{1,3})0$", main_code)
    return m.group(1).upper() if m else main_code.strip().upper()

# 期货代码正则:
#   主力连续  → 1-3 个字母 + 0           (如 RB0, AU0, AO0)
#   具体月份  → 1-3 个字母 + 3~4 位数字  (如 AO2501, RB2501, RB701)
_FUTURES_CODE_RE = re.compile(r"^[A-Za-z]{1,3}(?:0|\d{3,4})$")


def normalize_futures_symbol(symbol: str) -> str:
    """规整用户输入为期货代码 (大写).

    - 主力连续: 字母+0, 如 RB0 / AO0 / AU0
    - 具体月份: 字母+3~4位数字, 如 AO2501 / RB2501
    - 仅输入字母 (如 rb) 自动补 0 成主力连续 RB0
    - 支持 "RB0 螺纹钢" / "IF0 沪深300股指" 等 "代码 中文名" 格式
      (从开头提取代码, 中文名里的数字不会干扰)
    """
    raw = (symbol or "").strip()
    if not raw:
        return ""
    # 从开头提取: 字母 + (0主力 | 3~4位数字月份), 遇空格/中文即停
    m = re.match(r"^([A-Za-z]{1,3})(0|\d{3,4})", raw)
    if m:
        return m.group(0).upper()
    # 仅字母 (如 rb / au) 后接空格/结尾/中文 → 补 0 成主力连续
    m2 = re.match(r"^([A-Za-z]{1,3})(?:\s|$|[^A-Za-z0-9])", raw)
    if m2:
        return m2.group(1).upper() + "0"
    # 兜底: 清理后返回
    return re.sub(r"[\s\-_/]", "", raw).upper()


def _cn_now() -> datetime:
    return datetime.now(tz=_CN_TZ)


def _futures_session_open(now: datetime | None = None) -> bool:
    """国内期货交易时段判断 (用于 forming bar 实时刷新).

    日盘: 09:00-10:15, 10:30-11:30, 13:30-15:00 (周一至周五)
    夜盘 (按品种分三类, 均已覆盖):
      · 21:00-次日01:00  有色金属 (铜/铝/锌/铅/镍/锡/氧化铝AO/不锈钢/阴极铜)
      · 21:00-次日02:30  贵金属 (黄金AU/白银AG)、原油
      · 21:00-23:00      黑色/化工/农产品 (螺纹/铁矿/焦炭/甲醇/PTA/豆粕/白糖等)
    无夜盘: 股指 (IF/IC/IH)、国债 (T)
    """
    now = now or _cn_now()
    wd = now.weekday()
    t = now.hour * 60 + now.minute
    # 周六凌晨: 周五夜盘延续 (有色01:00 / 贵金属原油02:30)
    if wd == 5:  # 周六
        return t < 2 * 60 + 30
    if wd == 6:  # 周日全天不交易
        return False
    # 周一到周五
    # 日盘 (10:15-10:30 休息时段也算开盘中, 影响 forming bar 刷新)
    morning = 9 * 60 <= t < 11 * 60 + 30
    afternoon = 13 * 60 + 30 <= t < 15 * 60
    # 夜盘: 21:00-23:59 (所有夜盘品种) + 次日 00:00-02:30 (有色/贵金属/原油)
    night = 21 * 60 <= t < 23 * 60 + 59
    late_night = 0 <= t < 2 * 60 + 30  # 次日凌晨 (有色01:00 / 贵金属原油02:30)
    return morning or afternoon or night or late_night


class EastMoneyFuturesSource(DataSource):
    """国内期货主力合约 K 线 (AkShare 期货接口)."""

    def __init__(self) -> None:
        self._symbol: str = ""
        self._timeframe: str = ""
        self._connected: bool = False

    def connect(self) -> None:
        os.environ.setdefault("TQDM_DISABLE", "1")
        try:
            import akshare  # noqa: F401
        except ImportError as exc:
            raise DataSourceTransientError(
                "未安装 akshare, 请执行: pip install akshare"
            ) from exc
        self._connected = True
        logger.info("EastMoneyFuturesSource connected")

    def disconnect(self) -> None:
        self._connected = False
        logger.info("EastMoneyFuturesSource disconnected")

    def list_symbols(self) -> list[str]:
        """返回 "代码 中文名" 格式 (subscribe 时 normalize 自动提取代码)."""
        return [f"{code} {name}" for code, name in _SYMBOL_NAMES.items()]

    def variety_info(self) -> dict[str, dict[str, str]]:
        """返回所有预设品种的元信息 (交易所/合约月份规则/夜盘时段/品种前缀)."""
        info: dict[str, dict[str, str]] = {}
        for main_code, name in _SYMBOL_NAMES.items():
            prefix = variety_prefix(main_code)
            meta = _VARIETY_INFO.get(prefix, {})
            info[main_code] = {
                "name": name,
                "variety": prefix,
                "exchange": meta.get("exchange", ""),
                "months": meta.get("months", ""),
                "night": meta.get("night", ""),
            }
        return info

    def list_contracts(self, variety: str) -> list[str]:
        """查询某品种当前可交易的具体月份合约.

        输入: 品种前缀 (如 "AO") 或主力代码 (如 "AO0")
        返回: 在交易的具体月份合约列表 (如 ["AO2608","AO2609",...], 按到期顺序)
        注意: 会逐月探测, 耗时约 10-30 秒; 主力连续请直接用 "AO0".
        """
        if not self._connected:
            raise DataSourceTransientError("数据源未连接, 请先 connect()")
        import akshare as ak

        v = variety.strip().upper()
        # 从开头提取品种前缀 (处理 "AO0 氧化铝" / "AO0" / "AO" 等格式)
        _m = re.match(r"^([A-Za-z]{1,3})", v)
        prefix = _m.group(1) if _m else v
        now = _cn_now()
        candidates: list[str] = []
        for yy in (now.year % 100, (now.year + 1) % 100):
            for mm in range(1, 13):
                candidates.append(f"{prefix}{yy:02d}{mm:02d}")
        result: list[str] = []
        for code in candidates:
            self._throttle_akshare()
            try:
                df = ak.futures_zh_daily_sina(symbol=code.lower())
                if df is not None and not df.empty:
                    result.append(code)
            except Exception:
                pass
        return result

    def generate_contracts(self, variety: str) -> list[str]:
        """基于品种月份规则和当前日期, 生成可能的有效合约列表 (不探测网络, 即时返回).

        输入: 品种前缀 (如 "AO") 或主力代码 (如 "AO0")
        返回: ["AO0 主力", "AO2607", "AO2608", ...] 格式, 主力放首位.
        用于 UI 两级选择: 先选品种 → 再选该品种的具体合约.
        """
        v = variety.strip().upper()
        # 从开头提取品种前缀 (处理 "AO0 氧化铝" / "AO0" / "AO" 等格式)
        _m = re.match(r"^([A-Za-z]{1,3})", v)
        prefix = _m.group(1) if _m else v
        meta = _VARIETY_INFO.get(prefix, {})
        months_rule = meta.get("months", "1-12月")

        # 解析月份规则 → 有效月份列表
        if "除2/12月" in months_rule:
            valid_months = [m for m in range(1, 13) if m not in (2, 12)]
        elif "单月" in months_rule:
            valid_months = [1, 3, 5, 7, 9, 11]
        elif "1,3,5,7,8,9,11,12" in months_rule:
            valid_months = [1, 3, 5, 7, 8, 9, 11, 12]
        elif "3/6/9/12" in months_rule:
            valid_months = [3, 6, 9, 12]
        else:
            valid_months = list(range(1, 13))

        now = _cn_now()
        contracts: list[str] = [f"{prefix}0 主力"]
        for yy in (now.year % 100, (now.year + 1) % 100):
            for mm in valid_months:
                contracts.append(f"{prefix}{yy:02d}{mm:02d}")
        return contracts

    def supported_timeframes(self) -> list[str]:
        return list(_SUPPORTED_TIMEFRAMES)

    def subscribe(self, symbol: str, timeframe: str) -> None:
        if timeframe not in _SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"不支持的周期: {timeframe!r}. 请使用 {list(_SUPPORTED_TIMEFRAMES)} 之一"
            )
        code = normalize_futures_symbol(symbol)
        if not code:
            raise ValueError("期货代码无效, 请输入主力代码, 如 RB0(螺纹钢)、AU0(黄金)、CU0(铜)")
        self._symbol = code
        self._timeframe = timeframe
        logger.info("EastMoneyFuturesSource subscribed: %s %s", code, timeframe)

    def unsubscribe(self) -> None:
        self._symbol = ""
        self._timeframe = ""
        logger.info("EastMoneyFuturesSource unsubscribed")

    def is_symbol_available(self, symbol: str) -> bool:
        code = normalize_futures_symbol(symbol)
        return bool(_FUTURES_CODE_RE.match(code))

    def latest_snapshot(self, n: int) -> list[KlineBar]:
        if not self._connected:
            raise DataSourceTransientError("东方财富期货数据源未连接")
        if not self._symbol or not self._timeframe:
            raise DataSourceTransientError("未订阅期货品种/周期")

        fetch_n = max(n + 5, 30)
        try:
            rows_asc = self._fetch_history(self._symbol, self._timeframe, fetch_n)
        except DataSourceTransientError:
            raise
        except Exception as exc:
            logger.warning("东方财富期货拉取失败: %s", exc)
            raise DataSourceTransientError(f"期货数据拉取失败: {exc}") from exc

        # 过滤掉时间戳远超当前时间的异常K线 (akshare 偶尔返回未来交易日的数据)
        from pa_agent.data.bar_close_wait import timeframe_to_seconds
        now_ms = int(_cn_now().timestamp() * 1000)
        _tf_s = timeframe_to_seconds(self._timeframe) or 900
        _max_ts = now_ms + _tf_s * 1000  # 容差一个周期 (forming bar 结束时间可能略晚于当前)
        rows_asc = [r for r in rows_asc if int(r.get("ts_open", 0)) <= _max_ts]

        if not rows_asc:
            raise DataSourceTransientError(
                f"未返回期货数据: {self._symbol} {self._timeframe}"
            )

        if _futures_session_open():
            self._apply_spot_to_forming(rows_asc)

        rows_newest = list(reversed(rows_asc[-fetch_n:]))
        for i, row in enumerate(rows_newest):
            row["closed"] = not (i == 0 and _futures_session_open())

        return _rows_to_kline_bars(rows_newest, n)

    # ── Fetch ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _throttle_akshare() -> None:
        global _last_ak_fetch_mono
        now = time.monotonic()
        wait = _AK_MIN_INTERVAL_S - (now - _last_ak_fetch_mono)
        if wait > 0:
            time.sleep(wait)
        _last_ak_fetch_mono = time.monotonic()

    @staticmethod
    def _call_with_retries(
        label: str,
        fn: Any,
        *,
        attempts: int = 4,
        max_wait_s: float = 12.0,
    ) -> Any:
        last_exc: Exception | None = None
        waited = 0.0
        for i in range(attempts):
            EastMoneyFuturesSource._throttle_akshare()
            try:
                return fn()
            except Exception as exc:
                last_exc = exc
                if i + 1 >= attempts:
                    break
                delay = min(3.0, max(1.0, max_wait_s - waited))
                if delay <= 0:
                    break
                time.sleep(delay)
                waited += delay
                logger.debug("%s retry %d/%d: %s", label, i + 2, attempts, exc)
        assert last_exc is not None
        raise last_exc

    def _fetch_history(self, symbol: str, timeframe: str, n: int) -> list[dict[str, Any]]:
        if timeframe == "1d":
            return self._fetch_daily(symbol, n)
        if timeframe == "2h":
            # 2h 由 1h 重采样 (每 2 根合并)
            rows_60 = self._fetch_minute(symbol, "60", n * 2 + 8)
            return _resample_rows(rows_60, 2)[-n:]
        if timeframe == "4h":
            # 4h 由 1h 重采样
            rows_60 = self._fetch_minute(symbol, "60", n * 4 + 8)
            return _resample_rows_to_4h(rows_60)[-n:]
        # 分钟线
        period = _TF_MINUTE_PERIOD.get(timeframe, "15")
        return self._fetch_minute(symbol, period, n)

    def _fetch_daily(self, symbol: str, n: int) -> list[dict[str, Any]]:
        import akshare as ak

        df = self._call_with_retries(
            f"fut_daily {symbol}",
            lambda: ak.futures_zh_daily_sina(symbol=symbol),
        )
        norm = _normalize_ohlcv_df(df, time_col="date")
        if norm.empty:
            return []
        return _ashare_df_to_bars_asc(norm.tail(n + 5), time_col="date")

    def _fetch_minute(self, symbol: str, period: str, n: int) -> list[dict[str, Any]]:
        import akshare as ak

        df = self._call_with_retries(
            f"fut_min {symbol} {period}",
            lambda: ak.futures_zh_minute_sina(symbol=symbol, period=period),
        )
        norm = _normalize_ohlcv_df(df, time_col="time")
        if norm.empty:
            return []
        return _ashare_df_to_bars_asc(norm.tail(n + 8), time_col="time")

    def _apply_spot_to_forming(self, rows_asc: list[dict[str, Any]]) -> None:
        """交易时段内刷新最后一根 (forming) bar 的收盘价."""
        if not _futures_session_open() or not rows_asc:
            return
        price = self._fetch_realtime_price(self._symbol)
        if price is None:
            return
        last = rows_asc[-1]
        last["close"] = price
        last["high"] = max(last["high"], price)
        last["low"] = min(last["low"], price)

    def _fetch_realtime_price(self, symbol: str) -> float | None:
        """获取实时最新价 (futures_zh_realtime 返回全市场快照, 取目标合约)."""
        try:
            import akshare as ak

            df = self._call_with_retries(
                f"fut_rt {symbol}",
                lambda: ak.futures_zh_realtime(),
                attempts=2,
                max_wait_s=6.0,
            )
            if df is None or df.empty:
                return None
            # futures_zh_realtime 列: symbol/open/high/low/last_price/...
            sym_col = "symbol" if "symbol" in df.columns else df.columns[0]
            for _, row in df.iterrows():
                if str(row[sym_col]).strip().upper() == symbol.upper():
                    for cand in ("最新价", "last_price", "last", "close", "收盘"):
                        if cand in df.columns:
                            val = row[cand]
                            try:
                                return float(val)
                            except (TypeError, ValueError):
                                continue
                    return None
            return None
        except Exception as exc:
            logger.debug("期货实时价获取失败: %s", exc)
            return None
