from __future__ import annotations


def test_workspace_bootstrap_skips_data_and_ai_stacks(monkeypatch) -> None:
    from pa_agent.app_context import AppContext
    from pa_agent.config.settings import Settings

    settings = Settings()
    qclaw_syncs: list[object] = []
    cursor_syncs: list[object] = []

    monkeypatch.setattr("pa_agent.config.settings.load_settings", lambda _path: settings)
    monkeypatch.setattr("pa_agent.util.logging.configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(
        "pa_agent.ai.qclaw_connector.sync_qclaw_agent_provider_on_load",
        lambda value, **_kwargs: qclaw_syncs.append(value),
    )
    monkeypatch.setattr(
        "pa_agent.ai.cursor_connector.sync_cursor_provider_on_load",
        lambda value, **_kwargs: cursor_syncs.append(value),
    )
    monkeypatch.setattr(
        "pa_agent.data.factory.create_data_source",
        lambda _kind: (_ for _ in ()).throw(AssertionError("data source should be lazy")),
    )

    context = AppContext.bootstrap(workspace_only=True)

    assert context.settings is settings
    assert context.event_bus is not None
    assert context.data_source is None
    assert context.client is None
    assert context.assembler is None
    assert context.validator is None
    assert context.ledger is None
    assert qclaw_syncs == [settings]
    assert cursor_syncs == [settings]
