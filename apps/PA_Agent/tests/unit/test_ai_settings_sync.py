from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pa_agent.app_context import AppContext
from pa_agent.config.settings import AIProviderSettings, Settings
from pa_agent.gui.main_window import MainWindow


def test_sync_ai_model_settings_updates_only_target_terminal() -> None:
    source = Settings()
    source.provider = AIProviderSettings(
        model="new-primary",
        base_url="https://primary.example/v1",
        api_key="primary-key",
    )
    source.primary_provider_enabled = False
    source.retry_primary_each_analysis = True
    source.backup_provider_enabled = True
    source.backup_provider = AIProviderSettings(
        model="new-backup",
        base_url="https://backup.example/v1",
        api_key="backup-key",
    )

    terminal_settings = Settings()
    terminal_settings.general.last_symbol = "AAPL"
    terminal = SimpleNamespace(
        _ctx=AppContext(settings=terminal_settings),
        _debug_widget=SimpleNamespace(_api_key=""),
        _ai_sidebar=MagicMock(),
        _free_chat_session=None,
        _update_ai_mode_label=MagicMock(),
        _refresh_api_key_ui_state=MagicMock(),
    )
    new_client = MagicMock()

    with patch("pa_agent.ai.client_factory.create_ai_client", return_value=new_client):
        MainWindow.sync_ai_model_settings(terminal, source)

    assert terminal._ctx.settings.provider.model == "new-primary"
    assert terminal._ctx.settings.primary_provider_enabled is False
    assert terminal._ctx.settings.retry_primary_each_analysis is True
    assert terminal._ctx.settings.backup_provider_enabled is True
    assert terminal._ctx.settings.backup_provider is not None
    assert terminal._ctx.settings.backup_provider.model == "new-backup"
    assert terminal._ctx.settings.general.last_symbol == "AAPL"
    assert terminal._ctx.client is new_client
    assert terminal._debug_widget._api_key == "primary-key"


def test_resubmit_after_ai_settings_saved_cancels_stale_work() -> None:
    started = MagicMock()
    terminal = SimpleNamespace(
        _demo_mode=False,
        _cancel_snapshot_fetch_worker=MagicMock(),
        _cancel_analysis_worker=MagicMock(),
        _analysis_in_progress=True,
        analysis_state_changed=MagicMock(),
        _set_chart_refresh_paused=MagicMock(),
        _update_submit_button_state=MagicMock(),
        _symbol_combo=object(),
        _tf_combo=object(),
        _begin_submit_analysis=started,
    )

    MainWindow.resubmit_after_ai_settings_saved(terminal)

    terminal._cancel_snapshot_fetch_worker.assert_called_once_with()
    terminal._cancel_analysis_worker.assert_called_once()
    terminal.analysis_state_changed.emit.assert_called_once_with(False)
    started.assert_called_once_with(force_incremental=False)
