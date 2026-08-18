"""Centralised path constants for PA Agent.

All runtime directories are rooted at PROJECT_ROOT.
Import this module everywhere instead of hard-coding paths.
"""
from __future__ import annotations
from pathlib import Path

# ── Root ──────────────────────────────────────────────────────────────────────
# Resolve dynamically: this file is pa_agent/config/paths.py, so go up 3 levels.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent

# ── Prompt engineering assets (read-only at runtime) ─────────────────────────
PROMPT_DIR: Path = PROJECT_ROOT / "prompt_engineering"

# Alias kept for backward compat with design doc
PA_AGENT_DIR: Path = PROJECT_ROOT

# ── Runtime write directories ─────────────────────────────────────────────────
RECORDS_PENDING_DIR: Path = PROJECT_ROOT / "records" / "pending"
HISTORY_ANALYSIS_DIR: Path = PROJECT_ROOT / "history analysis"
EXPERIENCE_DIR: Path = PROJECT_ROOT / "experience"
CONFIG_DIR: Path = PROJECT_ROOT / "config"
LOGS_DIR: Path = PROJECT_ROOT / "logs"

# ── Individual file paths ─────────────────────────────────────────────────────
FEISHU_JSON_LEGACY_PATH: Path = CONFIG_DIR / "feishu.json"
SETTINGS_JSON_PATH: Path = CONFIG_DIR / "settings.json"
WATCHLIST_JSON_PATH: Path = CONFIG_DIR / "watchlist.json"
INSTRUMENT_TRADE_RULES_JSON_PATH: Path = CONFIG_DIR / "instrument_trade_rules.json"
TRADE_ENTRY_OVERRIDES_JSON_PATH: Path = CONFIG_DIR / "trade_entry_overrides.json"
PROMPT_LIBRARY_JSON_PATH: Path = CONFIG_DIR / "prompt_library.json"
LOG_FILE_PATH: Path = LOGS_DIR / "pa_agent.log"
CRASH_LOG_PATH: Path = LOGS_DIR / "crash.log"
