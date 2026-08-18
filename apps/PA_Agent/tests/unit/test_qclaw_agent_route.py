"""Tests for QClaw public-gateway Agent routing (no relay on 19004)."""
from __future__ import annotations

from unittest.mock import patch

from pa_agent.ai.qclaw_connector import (
    _PUBLIC_GATEWAY_MODEL,
    _resolve_qclaw_endpoint,
    apply_qclaw_provider_to_settings,
    is_openclaw_model,
    qclaw_provider_settings,
    should_use_qclaw_provider,
)


def test_is_openclaw_model_accepts_gateway_aliases() -> None:
    assert is_openclaw_model("openclaw")
    assert is_openclaw_model("openclaw/main")
    assert is_openclaw_model(" OpenClaw/Default ")
    assert not is_openclaw_model("pool-deepseek-v4-pro")


def test_should_use_qclaw_provider_when_base_url_matches_gateway() -> None:
    with patch("pa_agent.ai.qclaw_connector.detect_qclaw", return_value=True):
        with patch(
            "pa_agent.ai.qclaw_connector._get_qclaw_gateway_info",
            return_value=("127.0.0.1", 64257, "tok"),
        ):
            assert should_use_qclaw_provider(
                "pool-deepseek-v4-pro",
                "http://127.0.0.1:64257/v1",
            )
            assert not should_use_qclaw_provider(
                "pool-deepseek-v4-pro",
                "https://api.deepseek.com",
            )


def test_resolve_qclaw_endpoint_skips_relay_by_default() -> None:
    with patch("pa_agent.ai.qclaw_connector._fetch_public_gateway_models") as fetch:
        fetch.return_value = ["openclaw", "openclaw/main"]
        with patch("pa_agent.ai.qclaw_relay_manager.ensure_qclaw_relay") as ensure:
            base_url, model, mode = _resolve_qclaw_endpoint(
                "127.0.0.1",
                58579,
                "token",
            )
    ensure.assert_not_called()
    assert base_url == "http://127.0.0.1:58579/v1"
    assert model == _PUBLIC_GATEWAY_MODEL
    assert "Agent" in mode


def test_qclaw_provider_settings_uses_openclaw_agent() -> None:
    with patch("pa_agent.ai.qclaw_connector._get_qclaw_gateway_info") as info:
        info.return_value = ("127.0.0.1", 58579, "secret")
        with patch("pa_agent.ai.qclaw_connector._resolve_qclaw_endpoint") as resolve:
            resolve.return_value = (
                "http://127.0.0.1:58579/v1",
                _PUBLIC_GATEWAY_MODEL,
                "公开网关（OpenClaw Agent / openclaw）",
            )
            settings = qclaw_provider_settings()
    assert settings is not None
    assert settings.model == _PUBLIC_GATEWAY_MODEL
    assert settings.base_url == "http://127.0.0.1:58579/v1"
    resolve.assert_called_once()
    assert resolve.call_args.kwargs.get("prefer_relay") is False


def test_apply_qclaw_provider_keeps_openclaw_sub_agent_alias() -> None:
    from pa_agent.config.settings import Settings

    settings = Settings()
    with patch(
        "pa_agent.ai.qclaw_connector.qclaw_provider_settings",
        return_value=type(
            "P",
            (),
            {
                "model": "openclaw/main",
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
                err = apply_qclaw_provider_to_settings(
                    settings,
                    preferred_model="openclaw/main",
                )

    assert err is None
    assert settings.provider.model == "openclaw/main"


def test_apply_qclaw_provider_forces_agent_model() -> None:
    from pa_agent.config.settings import Settings

    settings = Settings()
    settings.provider.model = "openclaw"
    settings.provider.base_url = "http://127.0.0.1:1/v1"

    with patch(
        "pa_agent.ai.qclaw_connector.qclaw_provider_settings",
        return_value=type(
            "P",
            (),
            {
                "model": _PUBLIC_GATEWAY_MODEL,
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
                err = apply_qclaw_provider_to_settings(settings)

    assert err is None
    assert settings.provider.model == _PUBLIC_GATEWAY_MODEL
    assert settings.provider.base_url == "http://127.0.0.1:58579/v1"


def test_apply_qclaw_provider_on_load_keeps_sub_agent_alias() -> None:
    """Startup sync (no preferred_model) must preserve openclaw/<gateway-alias>."""
    from pa_agent.config.settings import Settings

    settings = Settings()
    settings.provider.model = "openclaw/main"
    settings.provider.base_url = "http://127.0.0.1:58579/v1"

    with patch(
        "pa_agent.ai.qclaw_connector.qclaw_provider_settings",
    ) as resolve:
        resolve.return_value = type(
            "P",
            (),
            {
                "model": "openclaw/main",
                "base_url": "http://127.0.0.1:58579/v1",
                "api_key": "tok",
                "thinking": True,
                "reasoning_effort": "max",
                "context_window": 2_000_000,
            },
        )()
        with patch("pa_agent.ai.qclaw_connector.detect_qclaw", return_value=True):
            with patch(
                "pa_agent.ai.qclaw_connector.qclaw_health_check_base",
                return_value=(True, "ok"),
            ):
                err = apply_qclaw_provider_to_settings(settings)

    assert err is None
    resolve.assert_called_once_with(model="openclaw/main", prefer_relay=False)
    assert settings.provider.model == "openclaw/main"


def test_sync_workbuddy_on_load_keeps_submodel() -> None:
    """sync_workbuddy_provider_on_load must not strip openclaw_wb/<api-model>."""
    from pathlib import Path

    from pa_agent.ai.workbuddy_connector import sync_workbuddy_provider_on_load
    from pa_agent.config.settings import Settings

    settings = Settings()
    settings.provider.model = "openclaw_wb/deepseek-v4-flash"
    settings.provider.base_url = "https://copilot.tencent.com/v2"
    settings.provider.api_key = "wb-token"

    with patch("pa_agent.ai.workbuddy_connector.detect_workbuddy", return_value=True), patch(
        "pa_agent.ai.workbuddy_connector.apply_workbuddy_provider_to_settings"
    ) as apply, patch("pa_agent.config.settings.save_settings") as save:
        apply.return_value = None
        sync_workbuddy_provider_on_load(settings, save_path=Path("settings.json"))
        apply.assert_called_once_with(settings)
        save.assert_not_called()

    assert settings.provider.model == "openclaw_wb/deepseek-v4-flash"


def test_openclaw_model_never_selects_workbuddy_on_stale_copilot_base() -> None:
    """Stale copilot base_url must not hijack ``openclaw`` → QClaw routing."""
    from pa_agent.ai.workbuddy_connector import (
        is_workbuddy_route,
        should_use_workbuddy_provider,
    )

    stale_copilot = "https://copilot.tencent.com/v2"
    with patch("pa_agent.ai.workbuddy_connector.detect_workbuddy", return_value=True):
        assert should_use_qclaw_provider("openclaw", stale_copilot)
        assert not should_use_workbuddy_provider("openclaw", stale_copilot)

    provider = type(
        "P",
        (),
        {"model": "openclaw", "base_url": stale_copilot},
    )()
    assert not is_workbuddy_route(provider)


def test_openclaw_wb_model_never_selects_qclaw_on_stale_gateway_base() -> None:
    """Stale QClaw gateway base_url must not hijack ``openclaw_wb`` → WorkBuddy."""
    from pa_agent.ai.workbuddy_connector import (
        is_workbuddy_route,
        should_use_workbuddy_provider,
    )

    stale_qclaw = "http://127.0.0.1:58579/v1"
    with patch("pa_agent.ai.qclaw_connector.detect_qclaw", return_value=True):
        with patch(
            "pa_agent.ai.qclaw_connector._get_qclaw_gateway_info",
            return_value=("127.0.0.1", 58579, "tok"),
        ):
            assert not should_use_qclaw_provider("openclaw_wb", stale_qclaw)
            assert should_use_workbuddy_provider("openclaw_wb", stale_qclaw)

    provider = type(
        "P",
        (),
        {"model": "openclaw_wb", "base_url": stale_qclaw},
    )()
    assert is_workbuddy_route(provider)


def test_sync_qclaw_on_load_skips_openclaw_wb_with_stale_gateway_base() -> None:
    """Startup QClaw sync must not rewrite openclaw_wb when base_url is stale."""
    from pa_agent.ai.qclaw_connector import sync_qclaw_agent_provider_on_load
    from pa_agent.config.settings import Settings

    settings = Settings()
    settings.provider.model = "openclaw_wb"
    settings.provider.base_url = "http://127.0.0.1:58579/v1"
    settings.provider.api_key = "wb-token"

    with patch("pa_agent.ai.qclaw_connector.detect_qclaw", return_value=True):
        with patch(
            "pa_agent.ai.qclaw_connector._get_qclaw_gateway_info",
            return_value=("127.0.0.1", 58579, "qclaw-tok"),
        ):
            with patch(
                "pa_agent.ai.qclaw_connector.apply_qclaw_provider_to_settings"
            ) as apply:
                sync_qclaw_agent_provider_on_load(settings)
                apply.assert_not_called()

    assert settings.provider.model == "openclaw_wb"
    assert settings.provider.api_key == "wb-token"
