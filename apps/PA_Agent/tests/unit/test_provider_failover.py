import json
from unittest.mock import MagicMock, patch

import pytest

from pa_agent.config.settings import AIProviderSettings, Settings
from pa_agent.orchestrator import two_stage
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken


def _orchestrator(settings: Settings, client: MagicMock | None = None) -> TwoStageOrchestrator:
    return TwoStageOrchestrator(
        client=client or MagicMock(),
        assembler=MagicMock(),
        router=MagicMock(),
        validator=MagicMock(),
        pending_writer=MagicMock(),
        exp_reader=MagicMock(),
        settings=settings,
    )


def _settings(*, retry_primary: bool) -> Settings:
    settings = Settings(
        provider=AIProviderSettings(model="primary", api_key="primary-key"),
        backup_provider=AIProviderSettings(model="backup", api_key="backup-key"),
        backup_provider_enabled=True,
        retry_primary_each_analysis=retry_primary,
    )
    settings.primary_provider_runtime_disabled = True
    return settings


def _stream(orchestrator: TwoStageOrchestrator):
    return orchestrator._stream_chat_resilient(
        [],
        on_reasoning_token=None,
        on_content_token=None,
        cancel_token=CancelToken(),
        thinking=False,
        reasoning_effort="low",
        stage_label="Stage 1",
    )


def test_retry_primary_each_analysis_restarts_from_primary() -> None:
    primary_client = MagicMock()
    primary_client.stream_chat.side_effect = RuntimeError("primary unavailable")
    backup_client = MagicMock()
    backup_client.stream_chat.return_value = "backup reply"
    orchestrator = _orchestrator(_settings(retry_primary=True), primary_client)

    with (
        patch("pa_agent.ai.client_factory.create_ai_client", return_value=backup_client),
        patch.object(orchestrator, "_notify_provider_switch"),
    ):
        assert _stream(orchestrator) == "backup reply"
        # A later stage in the same analysis stays on the selected backup.
        assert _stream(orchestrator) == "backup reply"

    primary_client.stream_chat.assert_called_once()
    assert backup_client.stream_chat.call_count == 2


def test_disabled_retry_skips_runtime_disabled_primary() -> None:
    primary_client = MagicMock()
    backup_client = MagicMock()
    backup_client.stream_chat.return_value = "backup reply"
    orchestrator = _orchestrator(_settings(retry_primary=False), primary_client)

    with (
        patch("pa_agent.ai.client_factory.create_ai_client", return_value=backup_client),
        patch.object(orchestrator, "_notify_provider_switch"),
    ):
        assert _stream(orchestrator) == "backup reply"

    primary_client.stream_chat.assert_not_called()
    backup_client.stream_chat.assert_called_once()


def test_manual_primary_disable_still_wins_over_retry_setting() -> None:
    settings = _settings(retry_primary=True)
    settings.primary_provider_enabled = False
    orchestrator = _orchestrator(settings)

    assert orchestrator._primary_route_disabled() is True


def test_truncated_provider_json_is_treated_as_route_failure() -> None:
    error = json.JSONDecodeError(
        "Unterminated string starting at",
        '{"x": "broken',
        6,
    )

    assert TwoStageOrchestrator._is_provider_failure(error) is True
    assert TwoStageOrchestrator._is_network_error(error) is True


def test_provider_switch_notification_is_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(two_stage, "_provider_switch_notification_sent_for_process", False)
    first = _orchestrator(_settings(retry_primary=False))
    second = _orchestrator(_settings(retry_primary=False))

    with patch("pa_agent.notify.feishu_notifier.send_provider_switch_alert") as notify:
        first._notify_provider_switch(stage_label="Stage 1", primary_error="first")
        second._notify_provider_switch(stage_label="Stage 1", primary_error="second")

    notify.assert_called_once()
