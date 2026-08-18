"""PushPlus 消息推送（配置在 settings.json 的 pushplus 段，无 GUI）。

文档：https://www.pushplus.plus/doc/ （一键请求 POST /send）
"""
from __future__ import annotations

import logging
import os
from html import escape
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pa_agent.config.settings import Settings

logger = logging.getLogger(__name__)

_PUSHPLUS_SEND_URL = "https://www.pushplus.plus/send"
_REQUEST_TIMEOUT_S = 15


def _pushplus_config_dict(settings: "Settings | None" = None) -> dict[str, Any]:
    if settings is not None:
        return settings.pushplus.model_dump()
    from pa_agent.config.paths import SETTINGS_JSON_PATH
    from pa_agent.config.settings import load_settings

    return load_settings(SETTINGS_JSON_PATH).pushplus.model_dump()


def resolve_pushplus_token(settings: "Settings | None" = None) -> str:
    """Token from settings.pushplus.token, else env PUSHPLUS_TOKEN."""
    cfg = _pushplus_config_dict(settings)
    token = (cfg.get("token") or "").strip()
    if not token:
        token = (os.environ.get("PUSHPLUS_TOKEN") or "").strip()
    return token


def pushplus_is_active(settings: "Settings | None" = None) -> bool:
    """True only when PushPlus is enabled and a token is configured."""
    cfg = _pushplus_config_dict(settings)
    if not cfg.get("enabled", False):
        return False
    return bool(resolve_pushplus_token(settings))


def send_pushplus_raw(
    title: str,
    html_content: str,
    *,
    token: str | None = None,
    settings: "Settings | None" = None,
) -> bool:
    """最底层的原始 PUSHPLUS 推送服务."""
    push_token = (token or resolve_pushplus_token(settings)).strip()
    if not push_token:
        logger.debug("PushPlus 未配置 token，跳过推送")
        return False

    payload = {
        "token": push_token,
        "title": title,
        "content": html_content,
        "template": "html",
    }
    try:
        import requests  # type: ignore[import]
    except ImportError:
        logger.warning("PushPlus：requests 库未安装，请运行 pip install requests")
        return False

    try:
        resp = requests.post(
            _PUSHPLUS_SEND_URL,
            json=payload,
            timeout=_REQUEST_TIMEOUT_S,
        )
        res = resp.json()
        if res.get("code") == 200:
            logger.info("PushPlus 推送成功: '%s'", title)
            return True
        logger.error("PushPlus 返回异常: %s", res.get("msg"))
    except Exception as exc:
        logger.error("发送 PushPlus 推送出错: %s", exc)
    return False


def _fmt(value: Any, default: str = "—") -> str:
    if value is None or value == "":
        return default
    return str(value)


def _truncate(text: str, max_len: int = 600) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _build_order_html(
    *,
    decision_inner: dict,
    stage2_full: dict,
    symbol: str,
    timeframe: str,
) -> str:
    dec = decision_inner or {}
    ncp: dict = stage2_full.get("next_cycle_prediction") or {}

    order_type = _fmt(dec.get("order_type"))
    order_dir = _fmt(dec.get("order_direction"))
    entry = _fmt(dec.get("entry_price"))
    stop = _fmt(dec.get("stop_loss_price"))
    tp = _fmt(dec.get("take_profit_price"))
    tp2 = _fmt(dec.get("take_profit_price_2"))
    reasoning = escape(_truncate((dec.get("reasoning") or "").strip(), 600))
    trade_conf = _fmt(dec.get("trade_confidence"))
    win_rate = _fmt(dec.get("estimated_win_rate"))
    watch_points: list = dec.get("watch_points") or []

    probs: dict = ncp.get("probabilities") or {}
    ncp_reasoning = escape(_truncate((ncp.get("reasoning") or "").strip(), 400))
    if probs:
        best_key = max(probs, key=lambda k: probs[k])
        next_cycle_str = f"{escape(str(best_key))}（概率 {probs[best_key]}）"
    elif ncp.get("cycle"):
        next_cycle_str = escape(_fmt(ncp.get("cycle")))
    else:
        next_cycle_str = "—"

    lines = [
        "<h3>PA Agent 下单信号</h3>",
        "<p>",
        f"<b>品种</b>：{escape(symbol)}　<b>周期</b>：{escape(timeframe)}<br>",
        f"<b>下单类型</b>：{escape(order_type)}　<b>方向</b>：{escape(order_dir)}<br>",
        f"<b>入场价</b>：{escape(entry)}　<b>止损</b>：{escape(stop)}　<b>TP1</b>：{escape(tp)}　<b>TP2</b>：{escape(tp2)}<br>",
        f"<b>置信度</b>：{escape(trade_conf)}　<b>预估胜率</b>：{escape(win_rate)}",
        "</p>",
    ]
    if reasoning:
        lines.extend(["<hr>", f"<p><b>决策理由</b><br>{reasoning.replace(chr(10), '<br>')}</p>"])
    lines.extend(["<hr>", f"<p><b>下一个市场周期预期</b>：{next_cycle_str}"])
    if ncp_reasoning:
        lines.append(f"<br>{ncp_reasoning.replace(chr(10), '<br>')}")
    lines.append("</p>")
    if watch_points:
        wp = "<br>".join(f"• {escape(_fmt(w))}" for w in watch_points[:5])
        lines.extend(["<hr>", f"<p><b>关注点</b><br>{wp}</p>"])
    return "\n".join(lines)


def send_order_signal(
    *,
    decision_inner: dict,
    stage2_full: dict,
    symbol: str,
    timeframe: str,
    settings: "Settings | None" = None,
) -> bool:
    """下单决策触发时向 PushPlus 推送 HTML 消息（与飞书并行，互不依赖）。"""
    if not pushplus_is_active(settings):
        return False

    title = f"PA Agent 下单信号 — {symbol} {timeframe}"
    html_content = _build_order_html(
        decision_inner=decision_inner,
        stage2_full=stage2_full,
        symbol=symbol,
        timeframe=timeframe,
    )
    return send_pushplus_raw(title, html_content, settings=settings)
