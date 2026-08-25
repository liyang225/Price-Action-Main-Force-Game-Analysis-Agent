"""Run DSA's own market-review process and consume its persisted contract."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from src.integration.dsa_market_context import (
    DEFAULT_DSA_DATABASE,
    load_latest_dsa_market_context,
)


DEFAULT_DSA_INSTALL_ROOT = Path(r"E:\Daily stock analysis")
DEFAULT_DSA_EXECUTABLE = (
    DEFAULT_DSA_INSTALL_ROOT
    / "resources"
    / "backend"
    / "stock_analysis"
    / "stock_analysis.exe"
)


def ensure_current_dsa_market_review(
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
    executable: Path | str = DEFAULT_DSA_EXECUTABLE,
    install_root: Path | str = DEFAULT_DSA_INSTALL_ROOT,
    timeout_seconds: float = 900.0,
    loader: Callable[..., dict[str, Any]] = load_latest_dsa_market_context,
    command_runner: Callable[..., Any] = subprocess.run,
    force: bool = False,
) -> dict[str, Any]:
    """Ensure DSA has a review for the current half-day cache window."""
    reference_time = now or datetime.now()
    configured = payload.get("dsa_database_path")
    database = Path(str(configured).strip()) if str(configured or "").strip() else DEFAULT_DSA_DATABASE
    current = loader(database, as_of=reference_time, max_age_days=0)
    if current.get("status") == "ready" and not force and _same_half_day(
        current.get("created_at"), reference_time
    ):
        return {"status": "ready", "triggered": False, "market": current}

    executable_path = Path(executable)
    root = Path(install_root)
    if not executable_path.is_file():
        raise FileNotFoundError(f"DSA backend executable not found: {executable_path}")
    if not root.is_dir():
        raise FileNotFoundError(f"DSA install directory not found: {root}")
    database_file = (
        database
        if database.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}
        else database / "stock_analysis.db"
    )
    environment = dict(os.environ)
    environment["DATABASE_PATH"] = str(database_file)
    env_file = root / ".env"
    if env_file.is_file():
        environment["ENV_FILE"] = str(env_file)

    completed = command_runner(
        [
            str(executable_path),
            "--market-review",
            "--no-notify",
            "--force-run",
        ],
        cwd=str(root),
        env=environment,
        capture_output=True,
        text=True,
        timeout=float(timeout_seconds),
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if int(getattr(completed, "returncode", 1)) != 0:
        detail = str(getattr(completed, "stderr", "") or getattr(completed, "stdout", ""))
        raise RuntimeError(f"DSA market review failed: {detail.strip()[-1000:]}")

    refreshed = loader(database, as_of=reference_time, max_age_days=0)
    if refreshed.get("status") != "ready":
        raise RuntimeError(
            "DSA process completed but did not persist a same-date market review: "
            + str(refreshed.get("reason") or refreshed.get("status"))
        )
    return {"status": "ready", "triggered": True, "market": refreshed}


def _same_half_day(created_at: object, reference_time: datetime) -> bool:
    """Treat the morning and afternoon trading sessions as separate cache windows."""
    if not isinstance(created_at, str) or not created_at.strip():
        # Older cache readers did not expose creation time; same-date readiness is
        # the only safe compatibility signal for those records.
        return True
    try:
        created = datetime.fromisoformat(created_at.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is not None and reference_time.tzinfo is None:
        created = created.replace(tzinfo=None)
    return created.date() == reference_time.date() and (created.hour < 12) == (
        reference_time.hour < 12
    )


__all__ = [
    "DEFAULT_DSA_EXECUTABLE",
    "DEFAULT_DSA_INSTALL_ROOT",
    "ensure_current_dsa_market_review",
]
