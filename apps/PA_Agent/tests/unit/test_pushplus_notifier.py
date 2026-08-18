"""Unit tests for PushPlus order notifications."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from pa_agent.config.settings import Settings
from pa_agent.notify.pushplus_notifier import (
    pushplus_is_active,
    resolve_pushplus_token,
    send_order_signal,
    send_pushplus_raw,
)


def test_send_pushplus_raw_skips_without_token() -> None:
    s = Settings()
    s.pushplus.token = ""
    with patch.dict("os.environ", {}, clear=True):
        assert send_pushplus_raw("t", "<p>x</p>", settings=s) is False


def test_send_pushplus_raw_success() -> None:
    s = Settings()
    s.pushplus.token = "test-token"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"code": 200, "msg": "ok"}

    with patch("requests.post", return_value=mock_resp) as post:
        ok = send_pushplus_raw("标题", "<p>内容</p>", settings=s)

    assert ok is True
    post.assert_called_once()
    payload = post.call_args.kwargs["json"]
    assert payload["token"] == "test-token"
    assert payload["title"] == "标题"
    assert payload["template"] == "html"


def test_resolve_pushplus_token_from_env() -> None:
    s = Settings()
    s.pushplus.token = ""
    with patch.dict("os.environ", {"PUSHPLUS_TOKEN": "env-token"}):
        assert resolve_pushplus_token(s) == "env-token"


def test_send_order_signal_respects_enabled_flag() -> None:
    s = Settings()
    s.pushplus.enabled = False
    s.pushplus.token = "tok"
    with patch("pa_agent.notify.pushplus_notifier.send_pushplus_raw") as raw:
        assert (
            send_order_signal(
                decision_inner={"order_type": "限价单"},
                stage2_full={},
                symbol="XAUUSDm",
                timeframe="15m",
                settings=s,
            )
            is False
        )
        raw.assert_not_called()


def test_pushplus_is_active_requires_enabled_and_token() -> None:
    s = Settings()
    s.pushplus.enabled = False
    s.pushplus.token = "tok"
    assert pushplus_is_active(s) is False
    s.pushplus.enabled = True
    s.pushplus.token = ""
    with patch.dict("os.environ", {}, clear=True):
        assert pushplus_is_active(s) is False
    s.pushplus.token = "tok"
    assert pushplus_is_active(s) is True
