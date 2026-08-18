"""Tests for Cursor subscription routing via QClaw (model alias openclaw_cs)."""
from __future__ import annotations

from unittest.mock import patch

from pa_agent.ai.cursor_connector import (
    apply_cursor_provider_to_settings,
    is_openclaw_cs_model,
    resolve_cursor_sdk_model_id,
    should_use_cursor_provider,
)
from pa_agent.ai.qclaw_connector import should_use_qclaw_provider
from pa_agent.ai.workbuddy_connector import should_use_workbuddy_provider


def test_is_openclaw_cs_model_accepts_aliases() -> None:
    assert is_openclaw_cs_model("openclaw_cs")
    assert is_openclaw_cs_model("openclaw_cs/main")
    assert is_openclaw_cs_model(" OpenClaw_CS ")
    assert not is_openclaw_cs_model("openclaw")
    assert not is_openclaw_cs_model("openclaw_wb")


def test_resolve_cursor_gateway_model_preserves_sub_alias() -> None:
    assert resolve_cursor_sdk_model_id("openclaw_cs/composer-2.5") == "composer-2.5"
    assert resolve_cursor_sdk_model_id("openclaw_cs") == "auto"
    assert resolve_cursor_sdk_model_id("deepseek-v4-pro") == "auto"


def test_should_use_cursor_provider_when_base_url_matches_gateway() -> None:
    with patch("pa_agent.ai.qclaw_connector.detect_qclaw", return_value=True):
        with patch(
            "pa_agent.ai.qclaw_connector._get_qclaw_gateway_info",
            return_value=("127.0.0.1", 64257, "tok"),
        ):
            assert should_use_cursor_provider(
                "openclaw_cs",
                "http://127.0.0.1:64257/v1",
            )
            # Explicit model alias should always trigger Cursor auto-config on Save,
            # regardless of a stale base_url the user typed.
            assert should_use_cursor_provider(
                "openclaw_cs",
                "https://api.deepseek.com",
            )


def test_openclaw_cs_never_selects_qclaw_or_workbuddy_on_stale_bases() -> None:
    stale_qclaw = "http://127.0.0.1:58579/v1"
    stale_copilot = "https://copilot.tencent.com/v2"
    with patch("pa_agent.ai.qclaw_connector.detect_qclaw", return_value=True):
        with patch(
            "pa_agent.ai.qclaw_connector._get_qclaw_gateway_info",
            return_value=("127.0.0.1", 58579, "tok"),
        ):
            assert not should_use_qclaw_provider("openclaw_cs", stale_qclaw)
            assert should_use_cursor_provider("openclaw_cs", stale_qclaw)
    with patch("pa_agent.ai.workbuddy_connector.detect_workbuddy", return_value=True):
        assert not should_use_workbuddy_provider("openclaw_cs", stale_copilot)
        assert should_use_cursor_provider("openclaw_cs")


def test_apply_cursor_provider_forces_openclaw_cs_model() -> None:
    from pa_agent.config.settings import Settings

    settings = Settings()
    settings.provider.model = "openclaw_cs"
    settings.provider.base_url = "http://127.0.0.1:1/v1"

    with patch(
        "pa_agent.ai.qclaw_connector.qclaw_provider_settings",
        return_value=type(
            "P",
            (),
            {
                "model": "openclaw_cs",
                "base_url": "http://127.0.0.1:58579/v1",
                "api_key": "tok",
                "thinking": True,
                "reasoning_effort": "max",
                "context_window": 2_000_000,
            },
        )(),
    ):
        with patch("pa_agent.ai.qclaw_connector.detect_qclaw", return_value=True):
            with patch(
                "pa_agent.ai.qclaw_connector.qclaw_health_check_base",
                return_value=(True, "ok"),
            ):
                err = apply_cursor_provider_to_settings(settings)

    # Bare openclaw_cs is ambiguous; user must supply agentId.
    assert err is not None
    assert settings.provider.model == "openclaw_cs"


def test_apply_cursor_provider_keeps_sub_alias() -> None:
    from pa_agent.config.settings import Settings

    settings = Settings()
    settings.provider.api_key = "crsr_test"
    err = apply_cursor_provider_to_settings(
        settings,
        preferred_model="openclaw_cs/main",
    )

    assert err is None
    assert settings.provider.model == "openclaw_cs/main"


def test_apply_cursor_rejects_openclaw_and_wb_hints() -> None:
    from pa_agent.config.settings import Settings

    settings = Settings()
    with patch("pa_agent.ai.qclaw_connector.detect_qclaw", return_value=True):
        err_openclaw = apply_cursor_provider_to_settings(
            settings,
            preferred_model="openclaw",
        )
        err_wb = apply_cursor_provider_to_settings(
            settings,
            preferred_model="openclaw_wb",
        )
    assert err_openclaw is not None
    assert err_wb is not None
