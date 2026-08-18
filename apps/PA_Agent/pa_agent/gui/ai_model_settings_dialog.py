"""AI 模型设置对话框 — 只包含 AI 提供商相关字段."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pa_agent.config.settings import (
    AIProviderSettings,
    Settings,
    normalize_provider_base_url,
    save_settings,
)
from pa_agent.config.paths import SETTINGS_JSON_PATH
from pa_agent.ai.cursor_connector import (
    is_openclaw_cs_model,
    should_use_cursor_provider,
)
from pa_agent.ai.qclaw_connector import (
    detect_qclaw,
    is_openclaw_model,
    should_use_qclaw_provider,
)
from pa_agent.ai.workbuddy_connector import (
    detect_workbuddy,
    is_openclaw_wb_model,
    should_use_workbuddy_provider,
)
from pa_agent.gui.widgets.toggle_switch import ToggleSwitch

class AIModelSettingsDialog(QDialog):
    """AI 模型 / 提供商配置对话框."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 模型设置")
        self.setMinimumWidth(520)
        self._settings = settings
        self._setup_ui()
        self._load_values()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        provider_group = QGroupBox("AI 提供商")
        form = QFormLayout(provider_group)

        self._primary_enabled_check = ToggleSwitch()
        form.addRow(
            "启用主线路:",
            self._toggle_row(self._primary_enabled_check, "优先使用主线路 API"),
        )

        self._retry_primary_each_analysis_check = ToggleSwitch()
        form.addRow(
            "每次分析重试主线路:",
            self._toggle_row(
                self._retry_primary_each_analysis_check,
                "每次分析先尝试主线路，失败后再切换备用线路",
            ),
        )

        self._model_edit = QLineEdit()
        form.addRow("模型 (model):", self._model_edit)

        self._base_url_edit = QLineEdit()
        self._base_url_edit.editingFinished.connect(self._load_max_tokens_for_current_url)
        form.addRow("Base URL:", self._base_url_edit)

        self._max_tokens_spin = QSpinBox()
        self._max_tokens_spin.setRange(0, 9_999_999)
        self._max_tokens_spin.setToolTip("按当前 Base URL 保存 max_tokens 上限；0 表示使用程序默认值。")
        form.addRow("Max Tokens 上限:", self._max_tokens_spin)

        api_key_row = QHBoxLayout()
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        self._api_key_edit.setPlaceholderText("输入 API Key")
        api_key_row.addWidget(self._api_key_edit)
        self._show_key_btn = QPushButton("隐藏")
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.setFixedWidth(52)
        self._show_key_btn.toggled.connect(self._toggle_api_key_visibility)
        api_key_row.addWidget(self._show_key_btn)
        form.addRow("API Key:", api_key_row)

        self._thinking_check = ToggleSwitch()
        form.addRow("Thinking:", self._toggle_row(self._thinking_check, "启用 Thinking"))

        self._reasoning_effort_combo = QComboBox()
        self._reasoning_effort_combo.addItems(["low", "medium", "high", "max"])
        form.addRow("Reasoning Effort:", self._reasoning_effort_combo)

        root.addWidget(provider_group)

        backup_group = QGroupBox("备用模型（主线路失败时自动切换）")
        backup_form = QFormLayout(backup_group)

        self._backup_enabled_check = ToggleSwitch()
        self._backup_enabled_check.toggled.connect(self._set_backup_fields_enabled)
        backup_form.addRow(
            "启用备用线路:",
            self._toggle_row(self._backup_enabled_check, "主线路 API 请求失败时重试备用线路"),
        )

        self._backup_model_edit = QLineEdit()
        backup_form.addRow("模型 (model):", self._backup_model_edit)

        self._backup_base_url_edit = QLineEdit()
        self._backup_base_url_edit.editingFinished.connect(
            self._load_backup_max_tokens_for_current_url
        )
        backup_form.addRow("Base URL:", self._backup_base_url_edit)

        backup_api_key_row = QHBoxLayout()
        self._backup_api_key_edit = QLineEdit()
        self._backup_api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        self._backup_api_key_edit.setPlaceholderText("输入备用 API Key")
        backup_api_key_row.addWidget(self._backup_api_key_edit)
        self._backup_show_key_btn = QPushButton("隐藏")
        self._backup_show_key_btn.setCheckable(True)
        self._backup_show_key_btn.setFixedWidth(52)
        self._backup_show_key_btn.toggled.connect(self._toggle_backup_api_key_visibility)
        backup_api_key_row.addWidget(self._backup_show_key_btn)
        backup_form.addRow("API Key:", backup_api_key_row)

        self._backup_max_tokens_spin = QSpinBox()
        self._backup_max_tokens_spin.setRange(0, 9_999_999)
        self._backup_max_tokens_spin.setToolTip(
            "按备用 Base URL 保存 max_tokens 上限；0 表示使用程序默认值。"
        )
        backup_form.addRow("Max Tokens 上限:", self._backup_max_tokens_spin)

        self._backup_thinking_check = ToggleSwitch()
        backup_form.addRow(
            "Thinking:", self._toggle_row(self._backup_thinking_check, "启用 Thinking")
        )

        self._backup_reasoning_effort_combo = QComboBox()
        self._backup_reasoning_effort_combo.addItems(["low", "medium", "high", "max"])
        backup_form.addRow("Reasoning Effort:", self._backup_reasoning_effort_combo)

        root.addWidget(backup_group)
        self._backup_editable_widgets = (
            self._backup_model_edit,
            self._backup_base_url_edit,
            self._backup_api_key_edit,
            self._backup_show_key_btn,
            self._backup_max_tokens_spin,
            self._backup_thinking_check,
            self._backup_reasoning_effort_combo,
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setText("保存")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _toggle_row(switch: ToggleSwitch, description: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(switch)
        layout.addWidget(QLabel(description))
        layout.addStretch()
        return row

    # ── 加载 / 保存 ────────────────────────────────────────────────────────────

    def _load_values(self) -> None:
        p = self._settings.provider
        self._primary_enabled_check.setChecked(
            bool(
                self._settings.primary_provider_enabled
                and not self._settings.primary_provider_runtime_disabled
            )
        )
        self._retry_primary_each_analysis_check.setChecked(
            bool(getattr(self._settings, "retry_primary_each_analysis", False))
        )
        self._model_edit.setText(p.model)
        self._base_url_edit.setText(p.base_url)
        self._load_max_tokens_for_current_url()
        self._api_key_edit.setText(p.api_key)
        self._thinking_check.setChecked(p.thinking)
        idx = self._reasoning_effort_combo.findText(p.reasoning_effort)
        if idx >= 0:
            self._reasoning_effort_combo.setCurrentIndex(idx)

        backup = self._settings.backup_provider
        self._backup_enabled_check.setChecked(
            bool(self._settings.backup_provider_enabled and backup is not None)
        )
        if backup is None:
            self._backup_model_edit.clear()
            self._backup_base_url_edit.clear()
            self._backup_api_key_edit.clear()
            self._backup_max_tokens_spin.setValue(0)
            self._backup_thinking_check.setChecked(p.thinking)
            self._backup_reasoning_effort_combo.setCurrentText(p.reasoning_effort)
        else:
            self._backup_model_edit.setText(backup.model)
            self._backup_base_url_edit.setText(backup.base_url)
            self._backup_load_max_tokens(backup.base_url, backup)
            self._backup_api_key_edit.setText(backup.api_key)
            self._backup_thinking_check.setChecked(backup.thinking)
            self._backup_reasoning_effort_combo.setCurrentText(backup.reasoning_effort)
        self._set_backup_fields_enabled(self._backup_enabled_check.isChecked())

    def _on_save(self) -> None:
        p = self._settings.provider
        self._settings.primary_provider_enabled = self._primary_enabled_check.isChecked()
        self._settings.retry_primary_each_analysis = (
            self._retry_primary_each_analysis_check.isChecked()
        )
        # A manual save is the explicit recovery action for a runtime-disabled route.
        self._settings.primary_provider_runtime_disabled = False
        model = self._model_edit.text().strip()
        base_url = self._base_url_edit.text().strip()
        api_key = self._api_key_edit.text().strip()

        # Explicit model aliases win over stale base_url (openclaw_wb before openclaw).
        if is_openclaw_wb_model(model) or should_use_workbuddy_provider(model, base_url):
            p.api_key = api_key
            err = self._apply_workbuddy_provider(preferred_model=model)
            if err:
                QMessageBox.warning(self, "WorkBuddy 配置异常", err)
                return
        elif is_openclaw_cs_model(model) or should_use_cursor_provider(model, base_url):
            # Cursor route must keep the user-provided Cursor API key (crsr_...).
            p.api_key = api_key
            err = self._apply_cursor_provider(preferred_model=model)
            if err:
                QMessageBox.warning(self, "Cursor 配置异常", err)
                return
        elif is_openclaw_model(model) or should_use_qclaw_provider(model, base_url):
            p.api_key = api_key
            err = self._apply_qclaw_provider(preferred_model=model)
            if err:
                QMessageBox.warning(self, "QClaw 配置异常", err)
                return
        else:
            field_err = self._validate_provider_fields(model, base_url)
            if field_err:
                QMessageBox.warning(self, "AI 提供商配置有误", field_err)
                return
            p.model = model
            p.base_url = base_url
            p.api_key = api_key

        p.thinking = self._thinking_check.isChecked()
        p.reasoning_effort = self._reasoning_effort_combo.currentText()  # type: ignore[assignment]
        self._save_max_tokens_for_url(p.base_url)

        backup_err = self._save_backup_provider()
        if backup_err:
            QMessageBox.warning(self, "备用模型配置有误", backup_err)
            return

        save_settings(self._settings, SETTINGS_JSON_PATH)
        self.accept()

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    def _load_max_tokens_for_current_url(self) -> None:
        key = normalize_provider_base_url(self._base_url_edit.text())
        value = 0
        if key:
            value = int((self._settings.provider.max_tokens_by_base_url or {}).get(key, 0) or 0)
        self._max_tokens_spin.setValue(value)

    def _load_backup_max_tokens_for_current_url(self) -> None:
        backup = self._settings.backup_provider
        self._backup_load_max_tokens(self._backup_base_url_edit.text(), backup)

    def _backup_load_max_tokens(
        self,
        base_url: str,
        provider: AIProviderSettings | None,
    ) -> None:
        key = normalize_provider_base_url(base_url)
        value = 0
        if key and provider is not None:
            value = int((provider.max_tokens_by_base_url or {}).get(key, 0) or 0)
        self._backup_max_tokens_spin.setValue(value)

    def _save_max_tokens_for_url(self, base_url: str) -> None:
        key = normalize_provider_base_url(base_url)
        if not key:
            return
        overrides = dict(self._settings.provider.max_tokens_by_base_url or {})
        value = self._max_tokens_spin.value()
        if value > 0:
            overrides[key] = value
        else:
            overrides.pop(key, None)
        self._settings.provider.max_tokens_by_base_url = overrides

    def _save_backup_max_tokens_for_url(
        self,
        provider: AIProviderSettings,
        base_url: str,
    ) -> None:
        key = normalize_provider_base_url(base_url)
        if not key:
            return
        overrides = dict(provider.max_tokens_by_base_url or {})
        value = self._backup_max_tokens_spin.value()
        if value > 0:
            overrides[key] = value
        else:
            overrides.pop(key, None)
        provider.max_tokens_by_base_url = overrides

    def _set_backup_fields_enabled(self, enabled: bool) -> None:
        for widget in self._backup_editable_widgets:
            widget.setEnabled(enabled)

    def _toggle_backup_api_key_visibility(self, checked: bool) -> None:
        if checked:
            self._backup_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._backup_show_key_btn.setText("显示")
        else:
            self._backup_api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self._backup_show_key_btn.setText("隐藏")

    def _save_backup_provider(self) -> str | None:
        enabled = self._backup_enabled_check.isChecked()
        self._settings.backup_provider_enabled = enabled
        if not enabled:
            return None

        model = self._backup_model_edit.text().strip()
        base_url = self._backup_base_url_edit.text().strip()
        api_key = self._backup_api_key_edit.text().strip()
        if not api_key:
            return "备用线路需要填写 API Key。"

        existing = self._settings.backup_provider
        provider = (
            existing.model_copy(deep=True)
            if existing is not None
            else AIProviderSettings()
        )
        provider.model = model
        provider.base_url = base_url
        provider.api_key = api_key

        # Resolve the same local aliases as the primary route, but on a
        # temporary Settings object so the primary provider is untouched.
        candidate = Settings(provider=provider)
        if is_openclaw_wb_model(model) or should_use_workbuddy_provider(model, base_url):
            err = self._apply_workbuddy_provider_to(candidate, model)
        elif is_openclaw_cs_model(model) or should_use_cursor_provider(model, base_url):
            err = self._apply_cursor_provider_to(candidate, model)
        elif is_openclaw_model(model) or should_use_qclaw_provider(model, base_url):
            err = self._apply_qclaw_provider_to(candidate, model)
        else:
            err = self._validate_provider_fields(model, base_url)
        if err:
            return err

        candidate.provider.thinking = self._backup_thinking_check.isChecked()
        candidate.provider.reasoning_effort = (
            self._backup_reasoning_effort_combo.currentText()
        )  # type: ignore[assignment]
        self._save_backup_max_tokens_for_url(candidate.provider, candidate.provider.base_url)
        self._settings.backup_provider = candidate.provider
        return None
    def focus_api_key_field(self) -> None:
        self._api_key_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self._api_key_edit.selectAll()

    def _toggle_api_key_visibility(self, checked: bool) -> None:
        if checked:
            self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._show_key_btn.setText("显示")
        else:
            self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self._show_key_btn.setText("隐藏")

    def _apply_cursor_provider(self, *, preferred_model: str = "") -> str | None:
        from pa_agent.ai.cursor_connector import apply_cursor_provider_to_settings
        return apply_cursor_provider_to_settings(self._settings, preferred_model=preferred_model or None)

    def _apply_qclaw_provider(self, *, preferred_model: str = "") -> str | None:
        from pa_agent.ai.qclaw_connector import apply_qclaw_provider_to_settings
        return apply_qclaw_provider_to_settings(self._settings, preferred_model=preferred_model or None)

    def _apply_workbuddy_provider(self, *, preferred_model: str = "") -> str | None:
        from pa_agent.ai.workbuddy_connector import apply_workbuddy_provider_to_settings
        return apply_workbuddy_provider_to_settings(self._settings, preferred_model=preferred_model or None)

    @staticmethod
    def _apply_cursor_provider_to(settings: Settings, preferred_model: str) -> str | None:
        from pa_agent.ai.cursor_connector import apply_cursor_provider_to_settings

        return apply_cursor_provider_to_settings(settings, preferred_model=preferred_model or None)

    @staticmethod
    def _apply_qclaw_provider_to(settings: Settings, preferred_model: str) -> str | None:
        from pa_agent.ai.qclaw_connector import apply_qclaw_provider_to_settings

        return apply_qclaw_provider_to_settings(settings, preferred_model=preferred_model or None)

    @staticmethod
    def _apply_workbuddy_provider_to(settings: Settings, preferred_model: str) -> str | None:
        from pa_agent.ai.workbuddy_connector import apply_workbuddy_provider_to_settings

        return apply_workbuddy_provider_to_settings(settings, preferred_model=preferred_model or None)

    @staticmethod
    def _validate_provider_fields(model: str, base_url: str) -> str | None:
        if is_openclaw_cs_model(model) or should_use_cursor_provider(model, base_url):
            return None
        if is_openclaw_model(model) or should_use_qclaw_provider(model, base_url):
            return None
        if is_openclaw_wb_model(model) or should_use_workbuddy_provider(model, base_url):
            return None
        if model.startswith(("http://", "https://")) and not base_url.startswith(("http://", "https://")):
            return (
                "「模型」与「Base URL」似乎填反了：\n"
                "• 模型应填模型名，如 deepseek-v4-pro 或 claude-sonnet-4-6\n"
                "• 使用 QClaw 时模型填 openclaw（或 openclaw/main）\n"
                "• 使用 Cursor 订阅时模型填 openclaw_cs\n"
                "• 使用 WorkBuddy 时模型填 openclaw_wb\n"
                "• Base URL 应填接口地址，如 https://api.deepseek.com"
            )
        if base_url.startswith(("http://", "https://")):
            return None
        if not base_url:
            if detect_qclaw():
                return (
                    "请填写 Base URL，或使用 QClaw/WorkBuddy：\n"
                    "• 模型填 openclaw → QClaw\n"
                    "• 模型填 openclaw_cs → Cursor 订阅（经 QClaw 网关）\n"
                    "• 模型填 openclaw_wb → WorkBuddy"
                )
            if detect_workbuddy():
                return "请填写 Base URL，或使用 WorkBuddy：\n• 模型填 openclaw_wb（保存时自动配置）"
            return "请填写 Base URL（API 接口地址）。"
        return (
            f"Base URL 不是有效网址（当前：{base_url}）。\n"
            "DeepSeek 示例：https://api.deepseek.com\n"
            "PackyAPI 示例：https://www.packyapi.com/v1\n"
            "QClaw：模型填 openclaw 后点保存（自动配置本地网关）\n"
            "Cursor：模型填 openclaw_cs 后点保存（经 QClaw 走 Cursor 订阅）\n"
            "WorkBuddy：模型填 openclaw_wb 后点保存（自动配置 WorkBuddy）"
        )

