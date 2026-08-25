"""Unit tests for settings load/save round-trip (task 2.4)."""
from __future__ import annotations
import json
from unittest.mock import patch

import pytest
from pathlib import Path
from pa_agent.config.settings import AIProviderSettings, Settings, load_settings, save_settings


def test_defaults(tmp_path):
    """load_settings on a missing file returns defaults and creates the file."""
    p = tmp_path / "settings.json"
    s = load_settings(p)
    assert s.provider.model == "openclaw_wb/deepseek-v4-flash"
    assert s.provider.base_url == "https://api.deepseek.com"
    assert s.provider.thinking is True
    assert s.provider.reasoning_effort == "high"
    assert s.provider.context_window == 2_000_000
    assert s.general.analysis_bar_count == 100
    assert s.general.last_symbol == "XAUUSDm"
    assert s.general.last_timeframe == "15m"
    assert not hasattr(s.general, "default_timeframe")
    assert s.general.decision_stance == "balanced"
    assert s.general.decision_flow_auto_play is True
    assert s.general.auto_resume_chart_after_analysis is False
    assert s.general.analysis_pool_tracking_on_start is True
    assert p.exists(), "defaults should be written to disk"


def test_round_trip(tmp_path):
    """save → load preserves all fields."""
    p = tmp_path / "settings.json"
    original = Settings()
    original.provider.api_key = "sk-test-1234"
    original.general.last_symbol = "BTCUSDT"
    save_settings(original, p)
    loaded = load_settings(p)
    assert loaded.provider.api_key == "sk-test-1234"
    # Crypto symbols migrate to gold defaults on load
    assert loaded.general.last_symbol == "XAUUSDm"
    assert loaded.provider.model == original.provider.model


def test_api_key_present_on_disk(tmp_path):
    """The saved JSON contains the plaintext API key."""
    p = tmp_path / "settings.json"
    s = Settings()
    s.provider.api_key = "sk-super-secret-key"
    save_settings(s, p)
    raw = p.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["provider"]["api_key"] == "sk-super-secret-key"


def test_corrupt_json_returns_defaults(tmp_path):
    """Corrupt settings.json falls back to defaults without raising."""
    p = tmp_path / "settings.json"
    p.write_text("{not valid json", encoding="utf-8")
    s = load_settings(p)
    assert s.provider.model == "openclaw_wb/deepseek-v4-flash"


def test_missing_api_key_leaves_api_key_blank(tmp_path):
    """If api_key is absent, api_key stays empty string."""
    p = tmp_path / "settings.json"
    data = Settings().model_dump()
    data["provider"].pop("api_key", None)
    data["provider"].pop("api_key_encrypted", None)
    p.write_text(json.dumps(data), encoding="utf-8")
    s = load_settings(p)
    assert s.provider.api_key == ""


def test_feishu_round_trip(tmp_path):
    """save → load preserves feishu settings."""
    p = tmp_path / "settings.json"
    original = Settings()
    original.feishu.webhook_url = "https://example.com/hook"
    original.feishu.secret = "sec"
    original.feishu.app_id = "cli_test"
    save_settings(original, p)
    loaded = load_settings(p)
    assert loaded.feishu.webhook_url == "https://example.com/hook"
    assert loaded.feishu.secret == "sec"
    assert loaded.feishu.app_id == "cli_test"


def test_pushplus_round_trip(tmp_path):
    """save → load preserves pushplus settings."""
    p = tmp_path / "settings.json"
    original = Settings()
    original.pushplus.token = "pp-test-token"
    original.pushplus.enabled = False
    save_settings(original, p)
    loaded = load_settings(p)
    assert loaded.pushplus.token == "pp-test-token"
    assert loaded.pushplus.enabled is False


def test_tushare_round_trip(tmp_path):
    """save → load preserves tushare token."""
    p = tmp_path / "settings.json"
    original = Settings()
    original.tushare.token = "ts-test-token"
    save_settings(original, p)
    loaded = load_settings(p)
    assert loaded.tushare.token == "ts-test-token"


def test_second_order_data_settings_round_trip(tmp_path):
    """Embedded SecondOrderGame credentials live in PA's single settings file."""
    p = tmp_path / "settings.json"
    original = Settings()
    original.second_order.tavily_api_key = "tvly-test-token"
    original.second_order.market_data_source = "akshare"
    original.second_order.sector_name = "半导体"
    original.second_order.sector_code = "sh.bk0001"
    original.second_order.symbol_preferences = {
        "SZ.159732": {
            "sector_name": "消费电子",
            "sector_code": "SZ.BK0002",
        }
    }
    original.second_order.dsa_database_path = r"E:\Daily stock analysis\data"
    original.second_order.run_news_prefetch_enabled = False
    original.second_order.run_material_preanalysis_enabled = False
    original.second_order.news_prefetch_enabled = True
    original.second_order.news_prefetch_schedule = "10:15"
    original.second_order.news_prefetch_interval_minutes = 12
    original.second_order.material_preanalysis_enabled = True
    original.second_order.material_preanalysis_schedule = "14:45"
    original.second_order.material_preanalysis_interval_minutes = 24
    original.futu.opend_host = "10.0.0.7"
    original.futu.opend_port = 12345

    save_settings(original, p)
    loaded = load_settings(p)

    assert loaded.second_order.tavily_api_key == "tvly-test-token"
    assert loaded.second_order.market_data_source == "akshare"
    assert loaded.second_order.sector_name == "半导体"
    assert loaded.second_order.sector_code == "sh.bk0001"
    assert loaded.second_order.symbol_preferences["SZ.159732"]["sector_name"] == "消费电子"
    assert loaded.second_order.symbol_preferences["SZ.159732"]["sector_code"] == "SZ.BK0002"
    assert loaded.second_order.dsa_database_path == r"E:\Daily stock analysis\data"
    assert loaded.second_order.run_news_prefetch_enabled is False
    assert loaded.second_order.run_material_preanalysis_enabled is False
    assert loaded.second_order.news_prefetch_enabled is True
    assert loaded.second_order.news_prefetch_schedule == "10:15"
    assert loaded.second_order.news_prefetch_interval_minutes == 12
    assert loaded.second_order.material_preanalysis_enabled is True
    assert loaded.second_order.material_preanalysis_schedule == "14:45"
    assert loaded.second_order.material_preanalysis_interval_minutes == 24
    assert loaded.futu.opend_host == "10.0.0.7"
    assert loaded.futu.opend_port == 12345


def test_second_order_settings_discard_legacy_news_keywords():
    from pa_agent.config.settings import SecondOrderSettings

    settings = SecondOrderSettings.model_validate(
        {
            "news_keyword": "旧默认关键词",
            "symbol_preferences": {
                "SH.600519": {
                    "sector_name": "白酒",
                    "sector_code": "SH.BK0003",
                    "news_keyword": "旧个股关键词",
                }
            },
        }
    )

    dumped = settings.model_dump()
    assert "news_keyword" not in dumped
    assert settings.symbol_preferences["SH.600519"] == {
        "sector_name": "白酒",
        "sector_code": "SH.BK0003",
    }


def test_pushplus_auto_disabled_when_enabled_without_token(tmp_path):
    """load_settings disables pushplus when enabled but token empty."""
    p = tmp_path / "settings.json"
    p.write_text(
        '{"pushplus": {"enabled": true, "token": ""}}',
        encoding="utf-8",
    )
    with patch.dict("os.environ", {}, clear=True):
        loaded = load_settings(p)
    assert loaded.pushplus.enabled is False
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["pushplus"]["enabled"] is False


def test_migrate_legacy_feishu_json(tmp_path):
    """Legacy config/feishu.json is merged into settings.json on load."""
    p = tmp_path / "settings.json"
    legacy = tmp_path / "feishu.json"
    save_settings(Settings(), p)
    legacy.write_text(
        json.dumps(
            {
                "enabled": True,
                "webhook_url": "https://example.com/legacy-hook",
                "secret": "legacy-secret",
                "app_id": "cli_legacy",
                "app_secret": "legacy-app-secret",
                "notify_on_order_only": True,
            }
        ),
        encoding="utf-8",
    )
    loaded = load_settings(p)
    assert loaded.feishu.webhook_url == "https://example.com/legacy-hook"
    assert loaded.feishu.secret == "legacy-secret"
    assert loaded.feishu.app_id == "cli_legacy"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["feishu"]["webhook_url"] == "https://example.com/legacy-hook"


def test_provider_max_tokens_by_base_url_is_normalized():
    settings = Settings.model_validate(
        {
            "provider": {
                "max_tokens_by_base_url": {
                    " HTTPS://Proxy.Example.com/v1/ ": "393216",
                    "https://proxy.example.com/default": 0,
                    "": 123,
                    "https://proxy.example.com/bad": "not-a-number",
                }
            }
        }
    )

    assert settings.provider.max_tokens_by_base_url == {
        "https://proxy.example.com/v1": 393_216
    }


def test_provider_max_tokens_by_base_url_defaults_to_empty():
    assert Settings().provider.max_tokens_by_base_url == {}


def test_backup_provider_round_trip_and_runtime_disable_is_not_persisted(tmp_path):
    p = tmp_path / "settings.json"
    original = Settings()
    original.primary_provider_enabled = True
    original.retry_primary_each_analysis = True
    original.primary_provider_runtime_disabled = True
    original.backup_provider_enabled = True
    original.backup_provider = AIProviderSettings(
        model="backup-model",
        base_url="https://backup.example/v1",
        api_key="backup-key",
        thinking=False,
        reasoning_effort="low",
        max_tokens_by_base_url={"https://backup.example/v1": 1234},
    )

    save_settings(original, p)
    loaded = load_settings(p)

    assert loaded.primary_provider_enabled is True
    assert loaded.retry_primary_each_analysis is True
    assert loaded.primary_provider_runtime_disabled is False
    assert loaded.backup_provider_enabled is True
    assert loaded.backup_provider is not None
    assert loaded.backup_provider.model == "backup-model"
    assert loaded.backup_provider.api_key == "backup-key"
    assert loaded.backup_provider.thinking is False
    assert loaded.backup_provider.reasoning_effort == "low"
    assert loaded.backup_provider.max_tokens_by_base_url == {
        "https://backup.example/v1": 1234
    }

