"""Pydantic settings models for PA Agent."""
from __future__ import annotations
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DecisionStance = Literal["conservative", "balanced", "aggressive", "extreme_aggressive"]
DataSourceKind = Literal["mt5", "tradingview", "akshare", "eastmoney", "eastmoney_futures", "tushare", "futu"]
NormalizationMode = Literal["strict", "lenient"]


def normalize_provider_base_url(base_url: str) -> str:
    """Return the stable key used for URL-scoped provider settings."""
    return str(base_url or "").strip().rstrip("/").lower()

class AIProviderSettings(BaseModel):
    """AI provider connection and behaviour settings."""
    model_config = ConfigDict(extra="ignore")

    model: str = "openclaw_wb/deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    api_key_encrypted: str = ""
    thinking: bool = True
    reasoning_effort: Literal["low", "medium", "high", "max"] = "high"
    context_window: int = 2_000_000
    max_tokens_by_base_url: dict[str, int] = Field(default_factory=dict)

    @field_validator("max_tokens_by_base_url", mode="before")
    @classmethod
    def _coerce_max_tokens_by_base_url(cls, v: object) -> dict[str, int]:
        if not isinstance(v, dict):
            return {}
        normalized: dict[str, int] = {}
        for raw_url, raw_value in v.items():
            key = normalize_provider_base_url(str(raw_url or ""))
            if not key:
                continue
            try:
                value = int(raw_value)
            except (TypeError, ValueError):
                continue
            if value > 0:
                normalized[key] = value
        return normalized


class PromptSettings(BaseModel):
    """Prompt assembly tuning (accuracy-oriented defaults)."""
    model_config = ConfigDict(extra="ignore")

    #: When True, Stage 2 loads every strategy .txt (legacy/test behaviour).
    stage2_load_full_strategy_library: bool = False
    experience_max_entries: int = Field(default=0, ge=0, le=10)
    experience_max_chars_per_entry: int = Field(default=400, ge=100, le=4000)
    #: Inject pattern判定表 + 速查 brief into Stage 1 user prompt (reduces missed tags).
    stage1_inject_pattern_briefs: bool = True


class ValidationSettings(BaseModel):
    """Post-LLM validation behaviour."""
    model_config = ConfigDict(extra="ignore")

    normalization_mode: NormalizationMode = "lenient"
    #: Stage-1 cross-field checks (gate trace, bar_by_bar, pattern tags). Off by default.
    stage1_coherence_checks: bool = False
    #: Stage-2 trace / diagnosis cross-checks (not order safety). Off by default.
    stage2_coherence_checks: bool = False
    trace_semantic_checks: bool = False
    strict_bar_by_bar_features: bool = False
    #: Allow Stage 1 truncated JSON tail repair before failing syntax validation.
    disable_truncation_repair: bool = False
    #: Re-call API with structured feedback when validation fails (format errors).
    retry_enabled: bool = True
    retry_max: int = Field(default=3, ge=0, le=5)
    #: Max retries for category=c semantic errors (subset only).
    retry_max_semantic: int = Field(default=1, ge=0, le=3)
    retry_stage2: bool = True


class GeneralSettings(BaseModel):
    """UI and data-feed general settings."""
    model_config = ConfigDict(extra="ignore")

    analysis_bar_count: int = Field(default=100, ge=2, le=5000)
    refresh_interval_ms: int = 1000
    context_warning_threshold_pct: float = 80.0
    last_data_source: DataSourceKind = "mt5"
    #: A-share K-line adjust for East Money / Baostock (qfq=前复权)
    kline_adjust: Literal["qfq", "hfq", "none"] = "qfq"
    #: TradingView 交易所；空字符串 =（自动）依次探测预设列表
    last_tradingview_exchange: str = ""
    last_symbol: str = "XAUUSDm"
    last_timeframe: str = "15m"
    decision_flow_auto_play: bool = True
    decision_flow_play_seconds: int = 50
    #: 阶段二给出限价/突破/市价单时：警报音、弹窗，并自动切到「决策」页（跳过决策树可视化演示）
    alert_on_order_opportunity: bool = True
    incremental_max_new_bars: int = Field(default=10, ge=0, le=500)
    #: 阶段二交易倾向：balanced=默认；conservative/aggressive 逐级调整下单意愿
    decision_stance: DecisionStance = "balanced"
    #: 决策树可视化：在「整图适配」基础上的缩放百分比（100=与适配一致；可任意放大，仅下限 10%）
    decision_flow_default_zoom_pct: int = Field(default=600, ge=10)
    #: 「实时」页思考过程/撰写回答框与追问输入框的等宽字体字号（pt）
    stream_pane_font_pt: int = Field(default=11, ge=8, le=28)
    #: K 线图上 #序号 标签的字号（pt）
    chart_seq_label_font_pt: int = Field(default=11, ge=6, le=24)
    #: 两阶段分析结束后是否自动恢复 K 线图表实时刷新
    auto_resume_chart_after_analysis: bool = False
    #: 持续跟踪分析：有新K线收盘时自动触发新一轮分析
    keep_analysis: bool = False
    #: 分析池工作区启动时是否默认开启全部股票的持续跟踪分析。
    analysis_pool_tracking_on_start: bool = True
    #: 重试后取消持续跟踪分析：校验失败触发重试后自动关闭 keep_analysis
    cancel_keep_analysis_on_retry: bool = False
    #: 交易决策置信度门槛：仅当 trade_confidence >= 此值时，才视为有下单机会（弹窗警报并提供决策详情）
    decision_confidence_threshold: int = Field(default=40, ge=0, le=100)
    #: 开启下根K线预期功能；关闭时不向模型请求该预测，节省 token
    enable_next_bar_prediction: bool = False
    #: 同一结构位 entry 相差≤3跳时，禁止反向新方案的冷却 K 线根数（已收盘）
    structure_flip_cooldown_bars: int = Field(default=3, ge=1, le=50)
    #: Persisted chart/sub-chart split ratios, keyed by each split hotzone id.
    split_hotzone_ratios: dict[str, float] = Field(default_factory=dict)

    @field_validator("last_data_source", mode="before")
    @classmethod
    def _coerce_legacy_data_source(cls, v: object) -> object:
        if v == "yfinance":
            return "mt5"
        if v in ("adata", "a_share"):
            return "akshare"
        if v == "eastmoney":
            return "eastmoney"
        if v == "tushare":
            return "tushare"
        return v

    @field_validator("decision_flow_default_zoom_pct", mode="before")
    @classmethod
    def _coerce_zoom_pct(cls, v: object) -> object:
        if v is None:
            return 50
        return v


_FEISHU_CONFIG_KEYS = (
    "enabled",
    "webhook_url",
    "secret",
    "app_id",
    "app_secret",
    "notify_on_order_only",
)


class FeishuSettings(BaseModel):
    """Feishu bot notification settings (persisted in settings.json)."""
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    webhook_url: str = ""
    secret: str = ""
    app_id: str = ""
    app_secret: str = ""
    #: True = only push when there is an order opportunity.
    notify_on_order_only: bool = True


class TushareSettings(BaseModel):
    """Tushare Pro data source settings (persisted in ignored settings.json)."""
    model_config = ConfigDict(extra="ignore")

    token: str = ""


class FutuSettings(BaseModel):
    """Futu OpenD endpoint settings."""
    model_config = ConfigDict(extra="ignore")

    opend_host: str = "127.0.0.1"
    opend_port: int = Field(default=11111, ge=1, le=65535)


class SecondOrderSettings(BaseModel):
    """Credentials owned by PA's embedded SecondOrderGame module."""
    model_config = ConfigDict(extra="ignore")

    tavily_api_key: str = ""
    trade_rules: str = ""
    market_data_source: Literal["futu", "akshare"] = "futu"
    sector_name: str = ""
    sector_code: str = ""
    symbol_preferences: dict[str, dict[str, str]] = Field(default_factory=dict)
    dsa_database_path: str = ""
    run_news_prefetch_enabled: bool = True
    run_material_preanalysis_enabled: bool = True
    news_prefetch_schedule: str = Field(
        default="09:35", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )
    material_preanalysis_schedule: str = Field(
        default="09:40", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )
    news_prefetch_enabled: bool = False
    news_prefetch_interval_minutes: int = Field(default=15, ge=1, le=120)
    max_news_items: int = Field(default=18, ge=5, le=30)
    material_preanalysis_enabled: bool = False
    material_preanalysis_interval_minutes: int = Field(default=30, ge=1, le=240)

    @field_validator("symbol_preferences", mode="before")
    @classmethod
    def _drop_legacy_news_keywords(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {
            key: (
                {
                    item_key: item_value
                    for item_key, item_value in item.items()
                    if item_key != "news_keyword"
                }
                if isinstance(item, dict)
                else item
            )
            for key, item in value.items()
        }


class PushPlusSettings(BaseModel):
    """PushPlus notification settings (settings.json only; no GUI)."""
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    token: str = ""


class Settings(BaseModel):
    """Root settings object persisted to config/settings.json."""
    model_config = ConfigDict(extra="ignore")

    provider: AIProviderSettings = Field(default_factory=AIProviderSettings)
    #: Primary route can be disabled by the user; runtime failover state is not persisted.
    primary_provider_enabled: bool = True
    #: Retry the primary route at the beginning of every analysis after a runtime failover.
    retry_primary_each_analysis: bool = False
    primary_provider_runtime_disabled: bool = Field(default=False, exclude=True)
    #: Optional secondary route used when the primary API request fails.
    backup_provider: AIProviderSettings | None = None
    backup_provider_enabled: bool = False
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    prompt: PromptSettings = Field(default_factory=PromptSettings)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    feishu: FeishuSettings = Field(default_factory=FeishuSettings)
    pushplus: PushPlusSettings = Field(default_factory=PushPlusSettings)
    tushare: TushareSettings = Field(default_factory=TushareSettings)
    futu: FutuSettings = Field(default_factory=FutuSettings)
    second_order: SecondOrderSettings = Field(default_factory=SecondOrderSettings)


def provider_api_key_configured(settings: Settings | None) -> bool:
    """Return True when a primary or enabled backup API key is loaded."""
    if settings is None:
        return False
    primary_enabled = bool(
        settings.primary_provider_enabled
        and not settings.primary_provider_runtime_disabled
    )
    if primary_enabled and (settings.provider.api_key or "").strip():
        return True
    backup = settings.backup_provider
    return bool(
        settings.backup_provider_enabled
        and backup is not None
        and (backup.api_key or "").strip()
    )


# ── Persistence ───────────────────────────────────────────────────────────────
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _migrate_legacy_feishu_json(raw: dict, settings_path: Path) -> bool:
    """Merge legacy config/feishu.json into settings.feishu when needed."""
    legacy_path = settings_path.parent / "feishu.json"
    if not legacy_path.exists():
        return False

    feishu = raw.setdefault("feishu", {})
    if (feishu.get("webhook_url") or "").strip():
        return False

    try:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("legacy feishu.json unreadable (%s); skipping migration", exc)
        return False

    migrated = False
    for key in _FEISHU_CONFIG_KEYS:
        if key not in legacy:
            continue
        value = legacy.get(key)
        if value in (None, ""):
            continue
        if feishu.get(key) in (None, ""):
            feishu[key] = value
            migrated = True
    if migrated:
        logger.info("Migrated Feishu config from %s into settings.json", legacy_path)
    return migrated


def load_settings(path: Path | None = None) -> "Settings":
    """Load settings from *path* (default: SETTINGS_JSON_PATH).

    Returns default Settings and writes them to disk if the file is absent.
    """
    from pa_agent.config.paths import SETTINGS_JSON_PATH

    path = path or SETTINGS_JSON_PATH

    if not path.exists():
        defaults = Settings()
        save_settings(defaults, path)
        return defaults

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("settings.json unreadable (%s); using defaults", exc)
        return Settings()

    # Migrate legacy field names
    general = raw.get("general", {})
    if "cost_warning_threshold_pct" in general and "context_warning_threshold_pct" not in general:
        general["context_warning_threshold_pct"] = general.pop("cost_warning_threshold_pct")
    general.pop("last_htf_text", None)
    from pa_agent.data.market_defaults import migrate_general_gold_defaults

    migrate_general_gold_defaults(general)
    if "default_bar_count" in general and "analysis_bar_count" not in general:
        general["analysis_bar_count"] = general.pop("default_bar_count")
    raw["general"] = general
    provider = raw.get("provider", {})
    provider.pop("pricing", None)
    raw["provider"] = provider

    # Migrate legacy encrypted key: drop it, api_key already in provider dict
    raw.setdefault("provider", {}).setdefault("api_key", "")

    migrated_feishu = _migrate_legacy_feishu_json(raw, path)
    settings = Settings.model_validate(raw)
    dirty = migrated_feishu
    if settings.pushplus.enabled and not settings.pushplus.token.strip():
        if not (os.environ.get("PUSHPLUS_TOKEN") or "").strip():
            settings.pushplus.enabled = False
            logger.info(
                "PushPlus enabled but token empty — auto-disabled "
                "(Feishu notifications unaffected)"
            )
            dirty = True
    if dirty:
        save_settings(settings, path)
    return settings


def save_settings(settings: "Settings", path: Path | None = None) -> None:
    """Persist settings to *path* (default: SETTINGS_JSON_PATH)."""
    from pa_agent.config.paths import SETTINGS_JSON_PATH

    path = path or SETTINGS_JSON_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    data = settings.model_dump()

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
