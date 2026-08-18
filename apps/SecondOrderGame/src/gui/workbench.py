"""Standalone HMM parameter workbench.

THESIS: one shared draft flows through beginner editing, expert inspection,
preview, and versioned save; the interface refuses disconnected wizard steps.
OWN-WORLD: PA Agent's cold graphite surfaces, steel-blue focus, dense native
controls, hairline separators, and readable data labels.
STORY: inspect the current prior, adjust it at the right level, verify its
consequences, then create an auditable version event.
FIRST VIEWPORT: editing workspace left, live evidence right, save and session
state fixed in the top bar.
FORM: desktop Operate workspace; established PA visual world.
FINISH: unreviewed and undocumented is unfinished; this build ends with the
finish review, the verdict, and DESIGN.md.
"""

from __future__ import annotations

from pathlib import Path
import sys

from PyQt6.QtCore import Qt, QSignalBlocker
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.config_validator import ConfigError
from src.gui.history import ConfigHistory, Snapshot
from src.gui.preview import PreviewData, build_preview
from src.gui.session import ConfigSession, RowRef
from src.gui.theme import WORKBENCH_QSS
from src.gui.widgets import DistributionWidget, HeatmapWidget
from src.labeler_constants import BEHAVIORS, CYCLE_STATES, PARTICIPANTS, behaviors_for


DEFAULT_CONFIG_PATH = Path(__file__).parents[2] / "config" / "hmm_prior.yaml"


class ParameterWorkbench(QMainWindow):
    """Native desktop editor for the versioned HMM prior configuration."""

    def __init__(self, config_path: Path | str = DEFAULT_CONFIG_PATH):
        super().__init__()
        self.config_path = Path(config_path).resolve()
        self.session = ConfigSession.from_file(self.config_path)
        self.history = ConfigHistory(self.config_path)
        self.beginner_sliders: dict[str, QSlider] = {}
        self._beginner_value_labels: dict[str, QLabel] = {}
        self._beginner_row_ref = RowRef("transition_matrix", CYCLE_STATES[0])
        self._expert_row_refs: dict[str, list[RowRef]] = {}
        self._last_saved_message: str | None = None
        self._invalid_expert_cells: set[tuple[str, int, int]] = set()
        self._preview_belief = dict(self.session.config["initial_belief"])

        self.setWindowTitle("SecondOrderGame · HMM 参数工作台")
        self.setMinimumSize(960, 620)
        self.resize(1380, 840)
        self.setStyleSheet(WORKBENCH_QSS)
        self._build_ui()
        self._populate_all()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_top_bar())
        root_layout.addWidget(self._build_main_area(), 1)
        root_layout.addWidget(self._build_status_bar())
        self.setCentralWidget(root)

    def _build_top_bar(self) -> QWidget:
        bar = QFrame(objectName="TopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 12, 16, 12)
        title_column = QVBoxLayout()
        title = QLabel("HMM 参数工作台", objectName="Title")
        self.version_label = QLabel(objectName="Subtle")
        title_column.addWidget(title)
        title_column.addWidget(self.version_label)
        layout.addLayout(title_column)
        layout.addStretch()

        self.discard_button = QPushButton("丢弃修改")
        self.discard_button.setToolTip("恢复到当前磁盘版本，不写入文件")
        self.discard_button.clicked.connect(self._discard)
        self.save_button = QPushButton("校验并保存", objectName="Primary")
        self.save_button.setToolTip("完整校验后自动升级版本并创建快照")
        self.save_button.clicked.connect(self._save)
        layout.addWidget(self.discard_button)
        layout.addWidget(self.save_button)
        return bar

    def _build_main_area(self) -> QWidget:
        horizontal = QSplitter(Qt.Orientation.Horizontal)
        horizontal.setChildrenCollapsible(False)
        horizontal.addWidget(self._build_editor_panel())
        horizontal.addWidget(self._build_preview_panel())
        horizontal.setStretchFactor(0, 3)
        horizontal.setStretchFactor(1, 2)
        horizontal.setSizes([800, 520])
        return horizontal

    def _build_editor_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 8, 12)
        layout.setSpacing(10)
        self.disclaimer_label = QLabel(
            "专家先验推演，非统计估计 · 修改只存在于当前会话，保存后才会写入配置",
            objectName="Disclaimer",
        )
        self.disclaimer_label.setWordWrap(True)
        layout.addWidget(self.disclaimer_label)
        self.validation_label = QLabel(objectName="ValidationError")
        self.validation_label.setWordWrap(True)
        self.validation_label.hide()
        layout.addWidget(self.validation_label)

        self.mode_tabs = QTabWidget()
        self.mode_tabs.addTab(self._build_beginner_tab(), "新手模式")
        self.mode_tabs.addTab(self._build_expert_tab(), "专家矩阵")
        self.mode_tabs.addTab(self._build_history_tab(), "版本历史")
        layout.addWidget(self.mode_tabs, 1)
        return panel

    def _build_beginner_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        row_header = QHBoxLayout()
        row_header.addWidget(QLabel("编辑概率行"))
        self.beginner_row_combo = QComboBox()
        for label, ref in self._all_row_choices():
            self.beginner_row_combo.addItem(label, ref)
        self.beginner_row_combo.currentIndexChanged.connect(self._beginner_choice_changed)
        row_header.addWidget(self.beginner_row_combo, 1)
        row_header.addWidget(QLabel("信心"))
        self.confidence_combo = QComboBox()
        self.confidence_combo.addItems(["弱", "中", "强"])
        self.confidence_combo.currentTextChanged.connect(self._confidence_changed)
        row_header.addWidget(self.confidence_combo)
        layout.addLayout(row_header)

        explainer = QLabel(
            "拖动一个比例时，其余项目按原有结构自动补偿；总和始终保持 100%。"
        )
        explainer.setObjectName("Subtle")
        explainer.setWordWrap(True)
        layout.addWidget(explainer)
        self.beginner_rows_widget = QWidget()
        self.beginner_rows_layout = QGridLayout(self.beginner_rows_widget)
        self.beginner_rows_layout.setColumnStretch(1, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.beginner_rows_widget)
        layout.addWidget(scroll, 1)
        return tab

    def _build_expert_tab(self) -> QWidget:
        tabs = QTabWidget()
        self.expert_tables: dict[str, QTableWidget] = {}
        specs = {
            "A": ("transition_matrix", tuple(CYCLE_STATES), tuple(CYCLE_STATES) + ("alpha",)),
            "C": (
                "confusion_matrix",
                tuple(f"true_{state}" for state in CYCLE_STATES),
                tuple(f"llm_{state}" for state in CYCLE_STATES) + ("alpha",),
            ),
            "W · 主力": (
                "behavior_mapping",
                tuple(CYCLE_STATES),
                behaviors_for("主力") + ("alpha",),
            ),
            "W · 散户": (
                "behavior_mapping",
                tuple(CYCLE_STATES),
                behaviors_for("散户") + ("alpha",),
            ),
        }
        for key, (section, row_labels, columns) in specs.items():
            table = QTableWidget(len(row_labels), len(columns))
            table.setAlternatingRowColors(True)
            table.setHorizontalHeaderLabels(columns)
            table.setVerticalHeaderLabels(row_labels)
            table.setToolTip("概率使用 0–1 小数；alpha 最小为 0.3")
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            table.itemChanged.connect(lambda item, table_key=key: self._expert_cell_changed(table_key, item))
            self.expert_tables[key] = table
            if key.startswith("W · "):
                participant = key.removeprefix("W · ")
                self._expert_row_refs[key] = [
                    RowRef(section, state, participant)
                    for state in CYCLE_STATES
                ]
            else:
                self._expert_row_refs[key] = [RowRef(section, row) for row in row_labels]
            tabs.addTab(table, key)
        return tabs

    def _build_history_tab(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.addWidget(QLabel("最近十个版本"))
        self.history_list = QListWidget()
        self.history_list.currentItemChanged.connect(self._history_selection_changed)
        left_layout.addWidget(self.history_list, 1)
        buttons = QHBoxLayout()
        self.compare_button = QPushButton("查看差异")
        self.compare_button.clicked.connect(self._show_selected_diff)
        self.restore_button = QPushButton("恢复为新版本")
        self.restore_button.setToolTip("恢复不会覆盖历史，而会创建一个新的版本事件")
        self.restore_button.clicked.connect(self._restore_selected)
        buttons.addWidget(self.compare_button)
        buttons.addWidget(self.restore_button)
        left_layout.addLayout(buttons)
        self.history_diff = QPlainTextEdit()
        self.history_diff.setReadOnly(True)
        self.history_diff.setPlaceholderText("选择一个快照查看与当前配置的差异。")
        splitter.addWidget(left)
        splitter.addWidget(self.history_diff)
        splitter.setSizes([260, 520])
        return splitter

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 12, 14, 12)
        self.preview_scope_label = QLabel(
            "仅用于参数效果预览，不影响生产运行，也不会随配置保存"
        )
        self.preview_scope_label.setObjectName("Subtle")
        self.preview_scope_label.setWordWrap(True)
        layout.addWidget(self.preview_scope_label)
        controls = QHBoxLayout()
        self.observation_label = QLabel("模拟观测")
        controls.addWidget(self.observation_label)
        self.observation_combo = QComboBox()
        self.observation_combo.addItems(CYCLE_STATES)
        self.observation_combo.setCurrentText("发酵")
        self.observation_combo.currentTextChanged.connect(self._refresh_preview)
        controls.addWidget(self.observation_combo)
        self.policy_label = QLabel("模拟政策环境")
        controls.addWidget(self.policy_label)
        self.policy_combo = QComboBox()
        self.policy_combo.addItems(["无干预", "政策暖风", "国家队托底中", "政策打压"])
        self.policy_combo.currentTextChanged.connect(self._refresh_preview)
        controls.addWidget(self.policy_combo, 1)
        layout.addLayout(controls)

        belief_panel = QWidget()
        belief_controls = QGridLayout(belief_panel)
        belief_controls.setContentsMargins(0, 0, 0, 0)
        belief_controls.addWidget(QLabel("当前板块信念"), 0, 0)
        self.belief_inputs: dict[str, QDoubleSpinBox] = {}
        for column, state in enumerate(CYCLE_STATES, 1):
            belief_controls.addWidget(QLabel(state), 0, column)
            field = QDoubleSpinBox()
            field.setRange(0.0, 100.0)
            field.setDecimals(1)
            field.setSuffix("%")
            field.setValue(self._preview_belief[state] * 100)
            field.setAccessibleName(f"{state}当前板块信念")
            field.valueChanged.connect(self._belief_changed)
            belief_controls.addWidget(field, 1, column)
            self.belief_inputs[state] = field
        belief_controls.addWidget(QLabel("合计需为 100%", objectName="Subtle"), 1, 0)
        layout.addWidget(belief_panel)

        self.preview_status_label = QLabel("预览已同步", objectName="PreviewStatus")
        layout.addWidget(self.preview_status_label)
        vertical = QSplitter(Qt.Orientation.Vertical)
        self.heatmap_tabs = QTabWidget()
        self.heatmap_widgets: dict[str, HeatmapWidget] = {}
        for key in ("A", "C", "W · 主力", "W · 散户"):
            widget = HeatmapWidget()
            self.heatmap_widgets[key] = widget
            heatmap_scroll = QScrollArea()
            heatmap_scroll.setWidgetResizable(True)
            heatmap_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            heatmap_scroll.setWidget(widget)
            self.heatmap_tabs.addTab(heatmap_scroll, key)
        vertical.addWidget(self.heatmap_tabs)
        behavior_group = QGroupBox("当前信念下的行为分布")
        behavior_layout = QVBoxLayout(behavior_group)
        self.distribution_widget = DistributionWidget()
        distribution_scroll = QScrollArea()
        distribution_scroll.setWidgetResizable(True)
        distribution_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        distribution_scroll.setWidget(self.distribution_widget)
        behavior_layout.addWidget(distribution_scroll)
        vertical.addWidget(behavior_group)
        vertical.setSizes([430, 300])
        layout.addWidget(vertical, 1)
        return panel

    def _build_status_bar(self) -> QWidget:
        bar = QFrame(objectName="StatusBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 7, 14, 7)
        self.session_state_label = QLabel("磁盘配置未修改")
        self.session_state_label.setObjectName("Subtle")
        layout.addWidget(self.session_state_label)
        layout.addStretch()
        self.path_label = QLabel(str(self.config_path), objectName="Subtle")
        self.path_label.setToolTip(str(self.config_path))
        layout.addWidget(self.path_label)
        return bar

    def _populate_all(self) -> None:
        self.version_label.setText(f"配置版本 v{self.session.base_version} · 未保存会话")
        self.show_beginner_row(self._beginner_row_ref)
        self._populate_expert_tables()
        self._refresh_history()
        self._refresh_preview()
        self._refresh_state()

    def show_beginner_row(self, ref: RowRef) -> None:
        self._beginner_row_ref = ref
        for index in reversed(range(self.beginner_rows_layout.count())):
            child = self.beginner_rows_layout.takeAt(index)
            if child.widget() is not None:
                child.widget().deleteLater()
        self.beginner_sliders.clear()
        self._beginner_value_labels.clear()
        row = self.session.row(ref)
        keys = [key for key in row if key != "alpha"]
        for index, key in enumerate(keys):
            label = QLabel(key.removeprefix("llm_"))
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(round(float(row[key]) * 100))
            slider.setAccessibleName(f"{key}百分比")
            value_label = QLabel(f"{float(row[key]):.1%}")
            value_label.setMinimumWidth(48)
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            slider.valueChanged.connect(lambda value, cell=key: self._beginner_slider_changed(cell, value))
            self.beginner_rows_layout.addWidget(label, index, 0)
            self.beginner_rows_layout.addWidget(slider, index, 1)
            self.beginner_rows_layout.addWidget(value_label, index, 2)
            self.beginner_sliders[key] = slider
            self._beginner_value_labels[key] = value_label
        self._sync_confidence(row.get("alpha", 8.0))

    def _beginner_choice_changed(self, index: int) -> None:
        ref = self.beginner_row_combo.itemData(index)
        if isinstance(ref, RowRef):
            self.show_beginner_row(ref)

    def _beginner_slider_changed(self, key: str, value: int) -> None:
        self.session.set_beginner_percentage(self._beginner_row_ref, key, value)
        self._sync_beginner_controls()
        self._after_edit()

    def _sync_beginner_controls(self) -> None:
        row = self.session.row(self._beginner_row_ref)
        for key, slider in self.beginner_sliders.items():
            blocker = QSignalBlocker(slider)
            slider.setValue(round(float(row[key]) * 100))
            del blocker
            self._beginner_value_labels[key].setText(f"{float(row[key]):.1%}")

    def _confidence_changed(self, label: str) -> None:
        if label:
            self.session.set_confidence(self._beginner_row_ref, label)
            self._after_edit()

    def _sync_confidence(self, alpha: float) -> None:
        label = "弱" if alpha < 5.5 else "中" if alpha < 16 else "强"
        blocker = QSignalBlocker(self.confidence_combo)
        self.confidence_combo.setCurrentText(label)
        del blocker

    def _populate_expert_tables(self) -> None:
        for key, table in self.expert_tables.items():
            blocker = QSignalBlocker(table)
            for row_index, ref in enumerate(self._expert_row_refs[key]):
                row = self.session.row(ref)
                for column_index, column in enumerate(self._table_columns(key)):
                    value = float(row[column])
                    item = QTableWidgetItem(f"{value:.6g}")
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    table.setItem(row_index, column_index, item)
            table.resizeColumnsToContents()
            del blocker

    def _expert_cell_changed(self, key: str, item: QTableWidgetItem) -> None:
        ref = self._expert_row_refs[key][item.row()]
        column = self._table_columns(key)[item.column()]
        cell = (key, item.row(), item.column())
        try:
            value = float(item.text())
        except ValueError:
            self._invalid_expert_cells.add(cell)
            self._show_local_error("矩阵单元必须是有限数字")
            self.save_button.setEnabled(False)
            self.preview_status_label.setText("预览暂停：当前会话包含非法输入")
            return
        try:
            self.session.set_expert_value(ref, column, value)
        except ValueError as error:
            self._invalid_expert_cells.add(cell)
            self._show_local_error(str(error))
        else:
            self._invalid_expert_cells.discard(cell)
        self._after_edit(populate_tables=False)

    def _after_edit(self, *, populate_tables: bool = True) -> None:
        self._last_saved_message = None
        if populate_tables:
            self._populate_expert_tables()
        self._refresh_state()
        self._refresh_preview()

    def _refresh_state(self) -> None:
        if self.session.is_valid and not self._invalid_expert_cells:
            self.validation_label.hide()
            self.save_button.setEnabled(self.session.is_dirty)
            if self._last_saved_message:
                self.session_state_label.setText(self._last_saved_message)
            elif self.session.is_dirty:
                self.session_state_label.setText("未保存修改 · 磁盘文件保持不变")
            else:
                self.session_state_label.setText("磁盘配置未修改")
        else:
            self._show_local_error(
                self.session.validation_error
                or "专家矩阵中仍有无法解析的单元，请修正后再保存"
            )
            self.save_button.setEnabled(False)
            self.session_state_label.setText("未保存修改 · 校验未通过")
        self.discard_button.setEnabled(self.session.is_dirty)
        self.version_label.setText(f"配置版本 v{self.session.base_version} · 未保存会话")

    def _show_local_error(self, message: str) -> None:
        self.validation_label.setText(f"无法应用：{message}")
        self.validation_label.show()

    def _refresh_preview(self, *args) -> None:
        if not self.session.is_valid or self._invalid_expert_cells:
            self.preview_status_label.setText("预览暂停：当前会话包含非法输入")
            for widget in self.heatmap_widgets.values():
                widget.set_data(None)
            self.distribution_widget.set_data(None)
            return
        if not self._belief_is_valid():
            self.preview_status_label.setText("预览暂停：当前板块信念合计必须为 100%")
            self.distribution_widget.set_data(None)
            return
        try:
            preview = build_preview(
                self.session.config,
                belief=self._preview_belief,
                observation=self.observation_combo.currentText(),
                policy=self.policy_combo.currentText(),
            )
        except (ConfigError, ValueError) as error:
            self.preview_status_label.setText(f"预览暂停：{error}")
            return
        self._apply_preview(preview)

    def _apply_preview(self, preview: PreviewData) -> None:
        for key, data in preview.heatmaps.items():
            self.heatmap_widgets[key].set_data(data)
        self.distribution_widget.set_data(preview.behavior_distributions)
        self.preview_status_label.setText("预览已同步")

    def _belief_changed(self, *args) -> None:
        self._preview_belief = {
            state: field.value() / 100.0 for state, field in self.belief_inputs.items()
        }
        self._refresh_preview()

    def _belief_is_valid(self) -> bool:
        return abs(sum(self._preview_belief.values()) - 1.0) <= 1e-6

    def _discard(self) -> None:
        self.session.discard()
        self._invalid_expert_cells.clear()
        self._last_saved_message = "已丢弃未保存修改"
        self._populate_all()

    def _save(self) -> None:
        try:
            event = self.history.save(self.session)
        except ConfigError as error:
            self._show_local_error(str(error))
            self._refresh_state()
            return
        self._last_saved_message = f"已保存 v{event.version} · 快照已记录"
        self._invalid_expert_cells.clear()
        self._populate_all()

    def _refresh_history(self) -> None:
        self.history_list.clear()
        snapshots = self.history.list_snapshots()
        for snapshot in snapshots:
            item = QListWidgetItem(f"v{snapshot.version}  {self._action_label(snapshot.action)}")
            item.setData(Qt.ItemDataRole.UserRole, snapshot)
            self.history_list.addItem(item)
        has_history = bool(snapshots)
        self.compare_button.setEnabled(has_history)
        self.restore_button.setEnabled(has_history)
        if not has_history:
            self.history_diff.setPlainText("首次保存后，这里会显示当前版本与前一版本。")

    def _history_selection_changed(self, current: QListWidgetItem | None, previous) -> None:
        enabled = current is not None
        self.compare_button.setEnabled(enabled)
        self.restore_button.setEnabled(enabled)

    def _selected_snapshot(self) -> Snapshot | None:
        item = self.history_list.currentItem()
        snapshot = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return snapshot if isinstance(snapshot, Snapshot) else None

    def _show_selected_diff(self) -> None:
        snapshot = self._selected_snapshot()
        if snapshot is not None:
            self.history_diff.setPlainText(self.history.compare(snapshot))

    def _restore_selected(self) -> None:
        snapshot = self._selected_snapshot()
        if snapshot is None:
            return
        try:
            event = self.history.restore(snapshot, self.session)
        except ConfigError as error:
            self._show_local_error(str(error))
            return
        self._last_saved_message = f"已恢复 v{snapshot.version} 为新版本 v{event.version}"
        self._populate_all()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.session.is_dirty:
            self.session.discard()
        event.accept()

    @staticmethod
    def _table_columns(key: str) -> tuple[str, ...]:
        if key == "A":
            return tuple(CYCLE_STATES) + ("alpha",)
        if key == "C":
            return tuple(f"llm_{state}" for state in CYCLE_STATES) + ("alpha",)
        if key.startswith("W · "):
            return behaviors_for(key.removeprefix("W · ")) + ("alpha",)
        raise ValueError(f"unknown expert table: {key}")

    @staticmethod
    def _all_row_choices() -> list[tuple[str, RowRef]]:
        rows = [(f"A · {state} 转移", RowRef("transition_matrix", state)) for state in CYCLE_STATES]
        rows.extend(
            (f"C · 真实{state}的观测", RowRef("confusion_matrix", f"true_{state}"))
            for state in CYCLE_STATES
        )
        rows.extend(
            (
                f"W · {state} · {participant}",
                RowRef("behavior_mapping", state, participant),
            )
            for state in CYCLE_STATES
            for participant in PARTICIPANTS
        )
        return rows

    @staticmethod
    def _action_label(action: str) -> str:
        if action == "save":
            return "保存"
        if action == "baseline":
            return "初始基线"
        if action.startswith("restore-v"):
            return f"恢复自 v{action.removeprefix('restore-v')}"
        return action


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("SecondOrderGame HMM 参数工作台")
    window = ParameterWorkbench()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
