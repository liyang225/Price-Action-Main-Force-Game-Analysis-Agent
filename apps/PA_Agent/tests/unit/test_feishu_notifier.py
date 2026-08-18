from __future__ import annotations

from unittest.mock import MagicMock, patch

from pa_agent.config.settings import Settings
from pa_agent.notify.feishu_notifier import (
    send_provider_failure_alert,
    send_provider_switch_alert,
)


def test_provider_failure_alert_sends_route_errors() -> None:
    settings = Settings()
    settings.feishu.enabled = True
    settings.feishu.webhook_url = "https://example.com/feishu-hook"
    response = MagicMock()
    response.json.return_value = {"code": 0}

    with patch("requests.post", return_value=response) as post:
        sent = send_provider_failure_alert(
            stage="Stage 2",
            primary_model="primary-model",
            backup_model="backup-model",
            primary_error="primary timeout",
            backup_error="backup 503",
            settings=settings,
        )

    assert sent is True
    payload = post.call_args.kwargs["json"]
    text = payload["content"]["text"]
    assert payload["msg_type"] == "text"
    assert "primary timeout" in text
    assert "backup 503" in text
    assert "backup-model" in text


def test_provider_failure_alert_skips_disabled_feishu() -> None:
    settings = Settings()
    settings.feishu.enabled = False

    with patch("requests.post") as post:
        sent = send_provider_failure_alert(
            stage="Stage 1",
            primary_model="primary-model",
            backup_model="backup-model",
            primary_error="primary error",
            backup_error="backup error",
            settings=settings,
        )

    assert sent is False
    post.assert_not_called()


def test_provider_switch_alert_includes_primary_error_and_backup_model() -> None:
    settings = Settings()
    settings.feishu.webhook_url = "https://example.com/feishu-hook"
    response = MagicMock()
    response.json.return_value = {"code": 0}

    with patch("requests.post", return_value=response) as post:
        sent = send_provider_switch_alert(
            stage="Stage 1",
            primary_model="primary-model",
            primary_error="primary timeout",
            backup_model="backup-model",
            settings=settings,
        )

    assert sent is True
    payload = post.call_args.kwargs["json"]
    text = payload["content"]["text"]
    assert "主线路模型：primary-model" in text
    assert "主线路错误原因：primary timeout" in text
    assert "已切换到备用线路：backup-model" in text
