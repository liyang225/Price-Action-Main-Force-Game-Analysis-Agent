"""飞书自定义机器人通知模块.

在下单决策触发时，向飞书群推送交互卡片消息，内容包含：
  - 品种 / 周期 / 方向 / 下单类型 / 入场价 / 止损 / TP1 / TP2
  - 交易置信度 / 预估胜率
  - 决策理由 (decision.reasoning)
  - 下一个市场周期预期及理由 (next_cycle_prediction)
  - K 线图表截图（先上传到飞书获取 image_key，再嵌入卡片）

使用方式
--------
1. 在飞书群里添加"自定义机器人"，复制 Webhook URL。
2. （可选）开启签名校验，复制 Secret。
3. （图片功能）在飞书开放平台创建企业自建应用，申请 im:resource 权限，
   获取 App ID 和 App Secret，在设置中填写（保存到 config/settings.json）。
4. 在程序菜单「飞书发送通知设置」中配置，或编辑 config/settings.json 的 feishu 段。

飞书官方文档
------------
自定义机器人：https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
图片上传：    https://open.feishu.cn/document/server-docs/im-v1/image/create
获取 tenant_access_token：
    https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import threading
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from pa_agent.config.settings import Settings

logger = logging.getLogger(__name__)

# ── 飞书 Open API 端点 ─────────────────────────────────────────────────────────
_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_IMAGE_UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/images"

# tenant_access_token 有效期 2 小时；提前 5 分钟刷新
_TOKEN_TTL_BUFFER_S = 300
_REQUEST_TIMEOUT_S = 12


# ── Token 缓存（进程内单例，线程安全）────────────────────────────────────────────
class _TokenCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str = ""
        self._expire_at: float = 0.0

    def get(self, app_id: str, app_secret: str) -> str | None:
        """返回有效的 tenant_access_token，过期则自动刷新."""
        with self._lock:
            if self._token and time.time() < self._expire_at:
                return self._token
            return self._refresh(app_id, app_secret)

    def _refresh(self, app_id: str, app_secret: str) -> str | None:
        try:
            import requests  # type: ignore[import]

            resp = requests.post(
                _TOKEN_URL,
                json={"app_id": app_id, "app_secret": app_secret},
                headers={"Content-Type": "application/json"},
                timeout=_REQUEST_TIMEOUT_S,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.warning("飞书 token 获取失败: %s", data)
                return None
            self._token = data["tenant_access_token"]
            expire = int(data.get("expire", 7200))
            self._expire_at = time.time() + expire - _TOKEN_TTL_BUFFER_S
            logger.debug("飞书 tenant_access_token 已刷新，有效期 %ds", expire)
            return self._token
        except Exception as exc:
            logger.warning("飞书 token 刷新异常: %s", exc)
            return None


_token_cache = _TokenCache()


# ── 配置加载 ──────────────────────────────────────────────────────────────────
def _feishu_config_dict(settings: "Settings | None" = None) -> dict[str, Any]:
    """Load Feishu settings from settings.json (or an in-memory Settings object)."""
    if settings is not None:
        return settings.feishu.model_dump()
    from pa_agent.config.paths import SETTINGS_JSON_PATH
    from pa_agent.config.settings import load_settings

    return load_settings(SETTINGS_JSON_PATH).feishu.model_dump()


# ── 签名 ──────────────────────────────────────────────────────────────────────
def _gen_sign(secret: str, timestamp: int) -> str:
    """按飞书规范计算 HmacSHA256 + Base64 签名.

    签名字符串：timestamp + "\\n" + secret
    """
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


# ── 图片上传 ──────────────────────────────────────────────────────────────────
def _upload_image(image_path: Path, app_id: str, app_secret: str) -> str | None:
    """上传 PNG 图片到飞书，返回 image_key 或 None.

    需要飞书企业自建应用，且已申请 im:resource 权限。
    """
    token = _token_cache.get(app_id, app_secret)
    if not token:
        logger.warning("飞书图片上传：无法获取 access_token，跳过图片")
        return None
    try:
        import requests  # type: ignore[import]

        with open(image_path, "rb") as f:
            resp = requests.post(
                _IMAGE_UPLOAD_URL,
                headers={"Authorization": f"Bearer {token}"},
                files={"image": (image_path.name, f, "image/png")},
                data={"image_type": "message"},
                timeout=_REQUEST_TIMEOUT_S,
            )
        data = resp.json()
        if data.get("code") == 0:
            key = data["data"]["image_key"]
            logger.info("飞书图片上传成功: %s -> %s", image_path.name, key)
            return key
        else:
            logger.warning("飞书图片上传失败: %s", data)
            return None
    except Exception as exc:
        logger.warning("飞书图片上传异常: %s", exc)
        return None


# ── 辅助格式化 ────────────────────────────────────────────────────────────────
def _fmt(value: Any, default: str = "—") -> str:
    """安全转字符串，空值返回 default."""
    if value is None or value == "":
        return default
    return str(value)


def _truncate(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _direction_style(order_dir: str) -> tuple[str, str]:
    """根据方向返回 (emoji_label, card_color)."""
    d = order_dir.lower()
    if any(k in d for k in ("bull", "多", "long", "buy")):
        return "🟢 做多", "green"
    if any(k in d for k in ("bear", "空", "short", "sell")):
        return "🔴 做空", "red"
    return f"📊 {order_dir}", "blue"


# ── 卡片构建 ──────────────────────────────────────────────────────────────────
def _build_card(
    decision_inner: dict,
    stage2_full: dict,
    symbol: str,
    timeframe: str,
    image_key: str | None,
) -> dict:
    """构建飞书卡片消息体（schema 2.0 interactive 类型）.

    结构：
      header  — 品种/周期 + 方向配色
      section — 下单参数（方向/类型/价格三元组）
      hr + markdown — 决策理由
      hr + markdown — 下一个市场周期预期
      hr + markdown — 关注点（可选）
      hr + img      — K线图表（可选）
    """
    dec = decision_inner or {}
    ncp: dict = stage2_full.get("next_cycle_prediction") or {}

    order_type = _fmt(dec.get("order_type"))
    order_dir = _fmt(dec.get("order_direction"))
    entry = _fmt(dec.get("entry_price"))
    stop = _fmt(dec.get("stop_loss_price"))
    tp = _fmt(dec.get("take_profit_price"))
    tp2 = _fmt(dec.get("take_profit_price_2"))
    reasoning = _truncate((dec.get("reasoning") or "").strip(), 600)
    trade_conf = _fmt(dec.get("trade_confidence"))
    win_rate = _fmt(dec.get("estimated_win_rate"))
    watch_points: list = dec.get("watch_points") or []

    dir_label, color = _direction_style(order_dir)

    # 下一个市场周期
    probs: dict = ncp.get("probabilities") or {}
    ncp_reasoning = _truncate((ncp.get("reasoning") or "").strip(), 400)
    if probs:
        best_key = max(probs, key=lambda k: probs[k])
        best_prob = probs[best_key]
        next_cycle_str = f"{best_key}（概率 {best_prob}）"
    elif ncp.get("cycle"):
        next_cycle_str = _fmt(ncp.get("cycle"))
    else:
        next_cycle_str = "—"

    # ── elements ──────────────────────────────────────────────────────────────
    elements: list[dict] = []

    # 摘要行
    elements.append(
        {
            "tag": "markdown",
            "content": (
                f"**品种**：{symbol}　**周期**：{timeframe}\n"
                f"**下单类型**：{order_type}　**方向**：{dir_label}\n"
                f"**入场价**：{entry}　**止损**：{stop}　**TP1**：{tp}　**TP2**：{tp2}\n"
                f"**置信度**：{trade_conf}　**预估胜率**：{win_rate}"
            ),
        }
    )

    # 决策理由
    if reasoning:
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "markdown",
                "content": f"**📝 决策理由**\n{reasoning}",
            }
        )

    # 下一个市场周期预期
    elements.append({"tag": "hr"})
    ncp_block = f"**🔮 下一个市场周期预期**：{next_cycle_str}"
    if ncp_reasoning:
        ncp_block += f"\n{ncp_reasoning}"
    elements.append({"tag": "markdown", "content": ncp_block})

    # 关注点
    if watch_points:
        wp_lines = "\n".join(f"• {_fmt(w)}" for w in watch_points[:5])
        elements.append({"tag": "hr"})
        elements.append(
            {"tag": "markdown", "content": f"**👁 关注点**\n{wp_lines}"}
        )

    # K线图表
    if image_key:
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "img",
                "img_key": image_key,
                "alt": {"tag": "plain_text", "content": f"K线图表 {symbol} {timeframe}"},
            }
        )

    card: dict = {
        "schema": "2.0",
        # 飞书卡片 2.0 仅支持 update_multi=true（共享卡片模式）
        "config": {"update_multi": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"🚨 PA Agent 下单信号 — {symbol} {timeframe}",
            },
            "template": color,
            "padding": "12px 12px 12px 12px",
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 12px 12px",
            "elements": elements,
        },
    }
    return {"msg_type": "interactive", "card": card}


# ── 主入口 ────────────────────────────────────────────────────────────────────
def send_order_signal(
    *,
    decision_inner: dict,
    stage2_full: dict,
    symbol: str,
    timeframe: str,
    chart_image_path: str | Path | None = None,
    settings: "Settings | None" = None,
) -> bool:
    """发送下单信号到飞书群.

    Parameters
    ----------
    decision_inner:
        stage2_decision["decision"] 内层字典。
    stage2_full:
        完整的 stage2_decision 字典（含 next_cycle_prediction）。
    symbol:
        交易品种，如 "XAUUSDm"。
    timeframe:
        K线周期，如 "15m"。
    chart_image_path:
        K线图表 PNG 的本地路径（可选）。需要在 settings.json 的 feishu 段配置
        app_id + app_secret 才能上传图片；未配置则仅发文字卡片。
    settings:
        可选的内存 Settings；未传时从 config/settings.json 读取。

    Returns
    -------
    bool
        发送成功返回 True，失败或未启用返回 False。
    """
    cfg = _feishu_config_dict(settings)

    if not cfg.get("enabled", True):
        logger.debug("飞书通知已禁用（settings.json feishu.enabled=false）")
        return False

    webhook_url = (cfg.get("webhook_url") or "").strip()
    if not webhook_url:
        logger.warning(
            "飞书通知：settings.json 未配置 feishu.webhook_url，跳过推送。"
            " 请在菜单「飞书发送通知设置」中完成配置。"
        )
        return False

    try:
        import requests  # type: ignore[import]
    except ImportError:
        logger.warning(
            "飞书通知：requests 库未安装，请运行 pip install requests"
        )
        return False

    # ── 图片上传（可选）──────────────────────────────────────────────────────
    image_key: str | None = None
    app_id = (cfg.get("app_id") or "").strip()
    app_secret = (cfg.get("app_secret") or "").strip()
    if chart_image_path and app_id and app_secret:
        p = Path(chart_image_path)
        if p.exists():
            image_key = _upload_image(p, app_id, app_secret)
        else:
            logger.debug("飞书通知：图片文件不存在，跳过上传: %s", chart_image_path)
    elif chart_image_path and not (app_id and app_secret):
        logger.debug(
            "飞书通知：chart_image_path 已设但 app_id/app_secret 未配置，"
            "发送无图片的卡片。"
        )

    # ── 构建消息体 ──────────────────────────────────────────────────────────
    payload = _build_card(
        decision_inner=decision_inner,
        stage2_full=stage2_full,
        symbol=symbol,
        timeframe=timeframe,
        image_key=image_key,
    )

    # ── 签名（可选）─────────────────────────────────────────────────────────
    secret = (cfg.get("secret") or "").strip()
    if secret:
        ts = int(time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = _gen_sign(secret, ts)

    # ── 发送 ─────────────────────────────────────────────────────────────────
    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=_REQUEST_TIMEOUT_S,
        )
        result = resp.json()
        # 飞书返回 code=0 或 StatusCode=0 均为成功
        if result.get("code") == 0 or result.get("StatusCode") == 0:
            logger.info(
                "飞书通知发送成功 [%s %s %s]",
                symbol,
                timeframe,
                decision_inner.get("order_type", "?"),
            )
            return True
        else:
            logger.warning(
                "飞书通知返回错误 [%s %s]: %s",
                symbol,
                timeframe,
                result,
            )
            return False
    except Exception as exc:
        logger.warning("飞书通知 HTTP 请求失败: %s", exc)
        return False


def _send_feishu_text_alert(
    text: str,
    *,
    settings: "Settings | None" = None,
    log_label: str,
) -> bool:
    """Send a text alert through the configured Feishu webhook."""
    cfg = _feishu_config_dict(settings)
    if not cfg.get("enabled", True):
        logger.debug("飞书通知已禁用，跳过 AI 线路通知")
        return False

    webhook_url = (cfg.get("webhook_url") or "").strip()
    if not webhook_url:
        logger.warning("飞书通知未配置 webhook_url，跳过 AI 线路通知")
        return False

    try:
        import requests  # type: ignore[import]
    except ImportError:
        logger.warning("飞书通知：requests 库未安装，跳过 AI 线路通知")
        return False

    payload: dict[str, Any] = {
        "msg_type": "text",
        "content": {"text": text},
    }
    secret = (cfg.get("secret") or "").strip()
    if secret:
        timestamp = int(time.time())
        payload["timestamp"] = str(timestamp)
        payload["sign"] = _gen_sign(secret, timestamp)

    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=_REQUEST_TIMEOUT_S,
        )
        result = resp.json()
        if result.get("code") == 0 or result.get("StatusCode") == 0:
            logger.info("飞书 %s发送成功", log_label)
            return True
        logger.warning("飞书 %s返回错误: %s", log_label, result)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("飞书 %s请求失败: %s", log_label, exc)
        return False


def send_provider_switch_alert(
    *,
    stage: str,
    primary_model: str,
    primary_error: str,
    backup_model: str,
    settings: "Settings | None" = None,
) -> bool:
    """Notify Feishu when analysis switches from primary to backup route."""
    text = (
        "PA Agent AI 线路切换通知\n"
        f"阶段：{_fmt(stage)}\n"
        f"主线路模型：{_fmt(primary_model)}\n"
        f"主线路错误原因：{_truncate(str(primary_error or '未知错误'), 800)}\n"
        f"已切换到备用线路：{_fmt(backup_model)}"
    )
    return _send_feishu_text_alert(
        text,
        settings=settings,
        log_label="AI 线路切换通知",
    )


def send_provider_failure_alert(
    *,
    stage: str,
    primary_model: str,
    backup_model: str,
    primary_error: str,
    backup_error: str,
    settings: "Settings | None" = None,
) -> bool:
    """Notify Feishu after the primary and backup AI routes both fail.

    This deliberately sends a plain text message instead of an order card so
    the alert remains useful even when the analysis did not produce a valid
    decision payload.
    """
    text = (
        "PA Agent AI 线路故障通知\n"
        f"阶段：{_fmt(stage)}\n"
        f"主线路模型：{_fmt(primary_model)}\n"
        f"备用线路模型：{_fmt(backup_model)}\n"
        f"主线路错误：{_truncate(str(primary_error or '未知错误'), 800)}\n"
        f"备用线路错误：{_truncate(str(backup_error or '未知错误'), 800)}\n"
        "主线路已在本次运行中临时关闭。"
    )
    return _send_feishu_text_alert(
        text,
        settings=settings,
        log_label="AI 线路故障通知",
    )
