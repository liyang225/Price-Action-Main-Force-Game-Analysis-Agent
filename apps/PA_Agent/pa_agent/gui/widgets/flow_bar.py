"""Compact pipeline progress strip for the analysis terminal."""
from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget


_DEFAULT_NAMES = ("数据", "快照", "诊断", "决策", "追问")
_STATUS_ICON = {"idle": "○", "done": "●", "active": "●", "error": "✕"}
_STATUS_COLOR = {
    "idle": "#646E7A",
    "done": "#00D084",
    "active": "#4A7EBB",
    "error": "#FF4757",
}


class _PipelineStep(QWidget):
    """One inline pipeline step: status dot and phase name."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._status = "idle"
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self._dot = QLabel()
        self._dot.setFixedWidth(11)
        self._dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._dot)

        self._name = QLabel(name)
        self._name.setStyleSheet(
            "font-size: 12px; font-weight: 600; color: #E8ECF1;"
        )
        self._name.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._name.setMinimumWidth(self._name.sizeHint().width())
        layout.addWidget(self._name)

        self.set_status("idle")

    def set_status(self, status: str) -> None:
        self._status = status if status in _STATUS_ICON else "idle"
        self._dot.setText(_STATUS_ICON[self._status])
        self._dot.setStyleSheet(
            f"font-size: 12px; color: {_STATUS_COLOR[self._status]}; border: none;"
        )

    def set_caption(self, text: str) -> None:
        """Retain the call surface while intentionally suppressing status captions."""
        del text

    @property
    def status(self) -> str:
        return self._status


class FlowBar(QWidget):
    """Five-stage, 32px pipeline strip with a breathing active state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("flowBar")
        self.setFixedHeight(32)
        self._pulse_visible = True
        self._timer = QTimer(self)
        self._timer.setInterval(750)
        self._timer.timeout.connect(self._pulse_active_steps)
        self._timer.start()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(1)
        self._steps: list[_PipelineStep] = []
        for index, name in enumerate(_DEFAULT_NAMES):
            step = _PipelineStep(name, self)
            layout.addWidget(step)
            self._steps.append(step)
            if index < len(_DEFAULT_NAMES) - 1:
                connector = QLabel()
                connector.setObjectName("pipelineConnector")
                connector.setFixedHeight(1)
                connector.setFixedWidth(18)
                layout.addWidget(connector)

    def _pulse_active_steps(self) -> None:
        self._pulse_visible = not self._pulse_visible
        for step in self._steps:
            if step.status == "active":
                color = "#5B8CC9" if self._pulse_visible else "#4A7EBB"
                step._dot.setStyleSheet(f"font-size: 12px; color: {color}; border: none;")

    def set_step_status(self, index: int, status: str) -> None:
        if 0 <= index < len(self._steps):
            self._steps[index].set_status(status)

    def set_step_caption(self, index: int, text: str) -> None:
        if 0 <= index < len(self._steps):
            self._steps[index].set_caption(text)

    def set_step_names(self, names: tuple[str, ...]) -> None:
        """Replace stage labels while preserving each step's current status."""
        if len(names) != len(self._steps):
            raise ValueError(f"expected {len(self._steps)} flow step names")
        for step, name in zip(self._steps, names):
            step._name.setText(str(name))
            step._name.setMinimumWidth(step._name.sizeHint().width())

    def reset_all(self) -> None:
        for step in self._steps:
            step.set_status("idle")

    def snapshot(self) -> tuple[str, ...]:
        """Return the current stage statuses for a module-local pipeline."""
        return tuple(step.status for step in self._steps)

    def restore(self, statuses: tuple[str, ...]) -> None:
        """Restore a previously saved module-local pipeline state."""
        if len(statuses) != len(self._steps):
            raise ValueError(f"expected {len(self._steps)} flow step statuses")
        for index, status in enumerate(statuses):
            self.set_step_status(index, status)
