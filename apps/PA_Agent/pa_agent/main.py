"""Application entry point for PA Agent."""
from __future__ import annotations

import logging
import sys

from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    # Early diagnostics before Qt / heavy imports: crash dumps + file logging.
    from pa_agent.util.crash_diagnostics import enable_crash_diagnostics, log_startup_diagnostics
    from pa_agent.util.logging import configure_logging

    enable_crash_diagnostics()
    configure_logging()
    log_startup_diagnostics()

    # OpenD is a local gateway used by the Futu data source. Its absence must
    # not prevent the rest of PA Agent from opening.
    from pa_agent.util.opend_launcher import ensure_opend_running
    ensure_opend_running()

    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("PA Agent")

    from pa_agent.gui.theme import apply_theme
    apply_theme(app)

    logger.info("PA Agent starting up")

    # The first screen only needs settings and the event bus.  Analysis tabs
    # create their own isolated data source and AI stack when first opened.
    from pa_agent.app_context import AppContext
    ctx = AppContext.bootstrap(workspace_only=True)

    # Update logging with the real API key now that settings are loaded
    if ctx.settings is not None:
        from pa_agent.util.logging import configure_logging
        configure_logging(api_key=ctx.settings.provider.api_key)
        from pa_agent.util.crash_diagnostics import log_startup_diagnostics
        log_startup_diagnostics()

    # Build the workspace. The original analysis terminal is hosted in stock tabs.
    from pa_agent.gui.workspace_window import WorkspaceWindow
    window = WorkspaceWindow(ctx)
    window.show()

    logger.info("Main window shown")
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
