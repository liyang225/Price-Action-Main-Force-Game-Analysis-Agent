"""Offscreen screenshot of the redesigned DecisionPanel (design verification only)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from pa_agent.gui.second_order_workspace import SecondOrderWorkspace
from pa_agent.gui.theme import apply_theme


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)

    panel = SecondOrderWorkspace()
    panel.resize(1400, 900)
    panel.show()
    app.processEvents()
    panel.grab().save("_design_review/second_order_workspace.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
