from __future__ import annotations

from unittest.mock import MagicMock, patch

from pa_agent.ai.cursor_connector import apply_cursor_provider_to_settings
from pa_agent.ai.qclaw_connector import apply_qclaw_provider_to_settings
from pa_agent.ai.workbuddy_connector import apply_workbuddy_provider_to_settings
from pa_agent.config.settings import Settings


def test_openclaw_overrides_user_url_and_key() -> None:
    """When model is openclaw*, user-filled base_url/api_key must be ignored."""
    s = Settings()
    s.provider.model = "openclaw"
    s.provider.base_url = "https://example.com/v1"
    s.provider.api_key = "sk-user-input"

    # Stub QClaw detection and settings resolution to avoid filesystem/network.
    with patch("pa_agent.ai.qclaw_connector.detect_qclaw", return_value=True), patch(
        "pa_agent.ai.qclaw_connector.qclaw_provider_settings"
    ) as resolve, patch("pa_agent.ai.qclaw_connector.qclaw_health_check_base", return_value=(True, "ok")):
        resolved = MagicMock()
        resolved.model = "openclaw"
        resolved.base_url = "http://127.0.0.1:51187/v1"
        resolved.api_key = "tok-from-qclaw"
        resolved.thinking = True
        resolved.reasoning_effort = "max"
        resolved.context_window = 2_000_000
        resolve.return_value = resolved

        err = apply_qclaw_provider_to_settings(s, preferred_model="openclaw")
        assert err is None

    assert s.provider.base_url == "http://127.0.0.1:51187/v1"
    assert s.provider.api_key == "tok-from-qclaw"


def test_openclaw_apply_preserves_user_thinking_prefs() -> None:
    """QClaw sync must not reset thinking/reasoning_effort from settings."""
    s = Settings()
    s.provider.model = "openclaw"
    s.provider.thinking = False
    s.provider.reasoning_effort = "low"

    with patch("pa_agent.ai.qclaw_connector.detect_qclaw", return_value=True), patch(
        "pa_agent.ai.qclaw_connector.qclaw_provider_settings"
    ) as resolve, patch("pa_agent.ai.qclaw_connector.qclaw_health_check_base", return_value=(True, "ok")):
        resolved = MagicMock()
        resolved.model = "openclaw"
        resolved.base_url = "http://127.0.0.1:51187/v1"
        resolved.api_key = "tok"
        resolved.thinking = True
        resolved.reasoning_effort = "max"
        resolved.context_window = 2_000_000
        resolve.return_value = resolved

        err = apply_qclaw_provider_to_settings(s, preferred_model="openclaw")
        assert err is None

    assert s.provider.thinking is False
    assert s.provider.reasoning_effort == "low"


def test_openclaw_wb_overrides_user_url_and_key() -> None:
    """When model is openclaw_wb*, user-filled base_url/api_key must be ignored."""
    s = Settings()
    s.provider.model = "openclaw_wb"
    s.provider.base_url = "https://example.com/v1"
    s.provider.api_key = "sk-user-input"

    with patch("pa_agent.ai.workbuddy_connector.detect_workbuddy", return_value=True), patch(
        "pa_agent.ai.workbuddy_connector.workbuddy_provider_settings"
    ) as resolve, patch(
        "pa_agent.ai.workbuddy_connector.workbuddy_health_check"
    ) as health_check:
        resolved = MagicMock()
        resolved.model = "openclaw_wb"
        resolved.base_url = "https://copilot.tencent.com/v2"
        resolved.api_key = "tok-from-workbuddy"
        resolved.thinking = True
        resolved.reasoning_effort = "max"
        resolved.context_window = 2_000_000
        resolve.return_value = resolved

        err = apply_workbuddy_provider_to_settings(s, preferred_model="openclaw_wb")
        assert err is None
        health_check.assert_not_called()

    assert s.provider.base_url == "https://copilot.tencent.com/v2"
    assert s.provider.api_key == "tok-from-workbuddy"


def test_openclaw_wb_apply_preserves_user_thinking_prefs() -> None:
    """WorkBuddy sync must not reset thinking/reasoning_effort from settings."""
    s = Settings()
    s.provider.model = "openclaw_wb/deepseek-v4-flash"
    s.provider.thinking = False
    s.provider.reasoning_effort = "medium"

    with patch("pa_agent.ai.workbuddy_connector.detect_workbuddy", return_value=True), patch(
        "pa_agent.ai.workbuddy_connector.workbuddy_provider_settings"
    ) as resolve, patch(
        "pa_agent.ai.workbuddy_connector.workbuddy_health_check",
        return_value=(True, "ok"),
    ):
        resolved = MagicMock()
        resolved.model = "openclaw_wb/deepseek-v4-flash"
        resolved.base_url = "https://copilot.tencent.com/v2"
        resolved.api_key = "tok"
        resolved.thinking = True
        resolved.reasoning_effort = "max"
        resolved.context_window = 2_000_000
        resolve.return_value = resolved

        err = apply_workbuddy_provider_to_settings(s)
        assert err is None

    assert s.provider.thinking is False
    assert s.provider.reasoning_effort == "medium"


def test_openclaw_wb_on_load_keeps_submodel_from_settings() -> None:
    """Startup sync (no preferred_model) must preserve openclaw_wb/<api-model>."""
    s = Settings()
    s.provider.model = "openclaw_wb/deepseek-v4-flash"
    s.provider.base_url = "https://copilot.tencent.com/v2"

    with patch("pa_agent.ai.workbuddy_connector.detect_workbuddy", return_value=True), patch(
        "pa_agent.ai.workbuddy_connector.workbuddy_provider_settings"
    ) as resolve, patch(
        "pa_agent.ai.workbuddy_connector.workbuddy_health_check",
        return_value=(True, "ok"),
    ):
        resolved = MagicMock()
        resolved.model = "openclaw_wb/deepseek-v4-flash"
        resolved.base_url = "https://copilot.tencent.com/v2"
        resolved.api_key = "tok-from-workbuddy"
        resolved.thinking = True
        resolved.reasoning_effort = "max"
        resolved.context_window = 2_000_000
        resolve.return_value = resolved

        err = apply_workbuddy_provider_to_settings(s)
        assert err is None
        resolve.assert_called_once_with(model="openclaw_wb/deepseek-v4-flash")

    assert s.provider.model == "openclaw_wb/deepseek-v4-flash"


def test_openclaw_cs_overrides_user_url_and_key() -> None:
    """When model is openclaw_cs*, user-filled base_url/api_key must be ignored."""
    s = Settings()
    s.provider.model = "openclaw_cs"
    s.provider.base_url = "https://example.com/v1"
    s.provider.api_key = "sk-user-input"

    with patch("pa_agent.ai.qclaw_connector.detect_qclaw", return_value=True), patch(
        "pa_agent.ai.qclaw_connector.qclaw_provider_settings"
    ) as resolve, patch("pa_agent.ai.qclaw_connector.qclaw_health_check_base", return_value=(True, "ok")):
        resolved = MagicMock()
        resolved.model = "openclaw_cs"
        resolved.base_url = "http://127.0.0.1:51187/v1"
        resolved.api_key = "tok-from-qclaw"
        resolved.thinking = True
        resolved.reasoning_effort = "max"
        resolved.context_window = 2_000_000
        resolve.return_value = resolved

        err = apply_cursor_provider_to_settings(s, preferred_model="openclaw_cs")
        assert err is None

    assert s.provider.base_url == "http://127.0.0.1:51187/v1"
    assert s.provider.api_key == "tok-from-qclaw"

