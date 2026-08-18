"""Tests for AI route failover and the legacy local fallbacks."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import openai
import pytest

from pa_agent.config.settings import AIProviderSettings, Settings
from pa_agent.orchestrator import two_stage
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from tests.fixtures.validators import schema_test_validator


@pytest.fixture(autouse=True)
def reset_provider_switch_notification_state(monkeypatch: pytest.MonkeyPatch):
    """Keep each test independent from the process-wide production guard."""
    monkeypatch.setattr(two_stage, "_provider_switch_notification_sent_for_process", False)


def test_stream_chat_retries_after_qclaw_fallback() -> None:
    settings = Settings()
    settings.provider.model = "openclaw"
    settings.provider.base_url = "http://127.0.0.1:53555/v1"

    client = MagicMock()
    client.stream_chat.side_effect = [
        openai.APIConnectionError(request=MagicMock(), message="Connection error."),
        MagicMock(content='{"gate_result":"wait"}', reasoning_content="", raw={}, usage=MagicMock(
            prompt_tokens=1, completion_tokens=1, total_tokens=2, cached_prompt_tokens=0
        ), latency_ms=1.0),
    ]

    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=MagicMock(),
        router=MagicMock(),
        validator=schema_test_validator(),
        pending_writer=MagicMock(),
        exp_reader=MagicMock(),
        settings=settings,
    )

    with patch.object(
        orchestrator,
        "_try_qclaw_fallback",
        return_value=True,
    ) as fallback:
        orchestrator._stream_chat_resilient(
            [{"role": "user", "content": "hi"}],
            on_reasoning_token=None,
            on_content_token=None,
            cancel_token=MagicMock(is_set=MagicMock(return_value=False)),
            thinking=True,
            reasoning_effort="max",
            stage_label="Stage 1",
        )

    fallback.assert_called_once()
    assert client.stream_chat.call_count == 2


def test_stream_chat_does_not_retry_when_qclaw_unavailable() -> None:
    settings = Settings()
    # Keep this test focused on the generic route; the application default may
    # be a local WorkBuddy alias with its own legacy fallback.
    settings.provider.model = "deepseek-v4-pro"
    client = MagicMock()
    client.stream_chat.side_effect = openai.APIConnectionError(
        request=MagicMock(),
        message="Connection error.",
    )

    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=MagicMock(),
        router=MagicMock(),
        validator=schema_test_validator(),
        pending_writer=MagicMock(),
        exp_reader=MagicMock(),
        settings=settings,
    )

    with patch.object(orchestrator, "_try_qclaw_fallback", return_value=False):
        try:
            orchestrator._stream_chat_resilient(
                [{"role": "user", "content": "hi"}],
                on_reasoning_token=None,
                on_content_token=None,
                cancel_token=MagicMock(is_set=MagicMock(return_value=False)),
                thinking=True,
                reasoning_effort="max",
                stage_label="Stage 1",
            )
        except openai.APIConnectionError:
            pass

    assert client.stream_chat.call_count == 1


def test_qclaw_fallback_skipped_for_non_openclaw_model() -> None:
    settings = Settings()
    settings.provider.model = "deepseek-v4-pro"

    orchestrator = TwoStageOrchestrator(
        client=MagicMock(),
        assembler=MagicMock(),
        router=MagicMock(),
        validator=schema_test_validator(),
        pending_writer=MagicMock(),
        exp_reader=MagicMock(),
        settings=settings,
    )

    with patch(
        "pa_agent.ai.qclaw_connector.apply_qclaw_provider_to_settings"
    ) as apply:
        assert not orchestrator._try_qclaw_fallback(original_model="deepseek-v4-pro")
        apply.assert_not_called()


def _orchestrator_with_backup(
    primary_client: MagicMock,
) -> tuple[TwoStageOrchestrator, Settings]:
    settings = Settings()
    settings.provider.model = "primary-model"
    settings.provider.api_key = "primary-key"
    settings.backup_provider = AIProviderSettings(
        model="backup-model",
        base_url="https://backup.example/v1",
        api_key="backup-key",
    )
    settings.backup_provider_enabled = True
    return (
        TwoStageOrchestrator(
            client=primary_client,
            assembler=MagicMock(),
            router=MagicMock(),
            validator=schema_test_validator(),
            pending_writer=MagicMock(),
            exp_reader=MagicMock(),
            settings=settings,
        ),
        settings,
    )


def test_stream_chat_uses_configured_backup_before_legacy_routes() -> None:
    primary = MagicMock()
    primary.stream_chat.side_effect = openai.APIConnectionError(
        request=MagicMock(), message="primary unavailable"
    )
    backup = MagicMock()
    backup.stream_chat.return_value = MagicMock(content="ok")
    orchestrator, settings = _orchestrator_with_backup(primary)

    with (
        patch("pa_agent.ai.client_factory.create_ai_client", return_value=backup),
        patch("pa_agent.notify.feishu_notifier.send_provider_switch_alert") as switch_alert,
        patch("pa_agent.notify.feishu_notifier.send_provider_failure_alert") as alert,
    ):
        result = orchestrator._stream_chat_resilient(
            [{"role": "user", "content": "hi"}],
            on_reasoning_token=None,
            on_content_token=None,
            cancel_token=MagicMock(is_set=MagicMock(return_value=False)),
            thinking=True,
            reasoning_effort="high",
            stage_label="Stage 1",
        )

    assert result.content == "ok"
    assert primary.stream_chat.call_count == 1
    assert backup.stream_chat.call_count == 1
    assert settings.primary_provider_runtime_disabled is True
    switch_alert.assert_called_once()
    assert switch_alert.call_args.kwargs["backup_model"] == "backup-model"
    alert.assert_not_called()


def test_stream_chat_notifies_when_backup_also_fails() -> None:
    primary = MagicMock()
    primary.stream_chat.side_effect = openai.APIConnectionError(
        request=MagicMock(), message="primary unavailable"
    )
    backup = MagicMock()
    backup.stream_chat.side_effect = openai.APIConnectionError(
        request=MagicMock(), message="backup unavailable"
    )
    orchestrator, settings = _orchestrator_with_backup(primary)

    with (
        patch("pa_agent.ai.client_factory.create_ai_client", return_value=backup),
        patch("pa_agent.notify.feishu_notifier.send_provider_switch_alert") as switch_alert,
        patch("pa_agent.notify.feishu_notifier.send_provider_failure_alert") as alert,
    ):
        with pytest.raises(openai.APIConnectionError):
            orchestrator._stream_chat_resilient(
                [{"role": "user", "content": "hi"}],
                on_reasoning_token=None,
                on_content_token=None,
                cancel_token=MagicMock(is_set=MagicMock(return_value=False)),
                thinking=True,
                reasoning_effort="high",
                stage_label="Stage 2",
            )

    assert settings.primary_provider_runtime_disabled is True
    switch_alert.assert_called_once()
    assert "primary unavailable" in switch_alert.call_args.kwargs["primary_error"]
    alert.assert_called_once()
    assert alert.call_args.kwargs["stage"] == "Stage 2"
    assert "backup unavailable" in alert.call_args.kwargs["backup_error"]
