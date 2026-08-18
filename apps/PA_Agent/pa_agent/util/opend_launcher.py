"""Start the local Futu OpenD application when PA Agent starts."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

OPEND_PATH_ENV = "FUTU_OPEND_PATH"
_PROCESS_NAMES = ("Futu_OpenD.exe", "FutuOpenD.exe")


def _without_console_flags() -> int:
    """Return the Windows flag that prevents the process check flashing a console."""
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def is_opend_running(process_names: Iterable[str] = _PROCESS_NAMES) -> bool:
    """Return whether one of the known Futu OpenD processes is running."""
    if sys.platform != "win32":
        return False

    for process_name in process_names:
        try:
            result = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    f"IMAGENAME eq {process_name}",
                    "/FO",
                    "CSV",
                    "/NH",
                ],
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
                creationflags=_without_console_flags(),
            )
        except OSError as exc:
            logger.warning("Unable to inspect Futu OpenD process state: %s", exc)
            return False

        if result.returncode == 0 and f'"{process_name}"'.casefold() in result.stdout.casefold():
            return True

    return False


def _candidate_paths() -> list[Path]:
    candidates: list[Path] = []

    configured_path = os.environ.get(OPEND_PATH_ENV, "").strip().strip('"')
    if configured_path:
        candidates.append(Path(configured_path).expanduser())

    roots_and_subdirs = (
        (os.environ.get("APPDATA"), ("Futu_OpenD", "FutuOpenD")),
        (os.environ.get("LOCALAPPDATA"), ("Futu_OpenD", "FutuOpenD")),
        (os.environ.get("ProgramFiles"), ("Futu_OpenD", "FutuOpenD", "Futu\\FutuOpenD")),
        (
            os.environ.get("ProgramFiles(x86)"),
            ("Futu_OpenD", "FutuOpenD", "Futu\\FutuOpenD"),
        ),
    )
    for root, subdirs in roots_and_subdirs:
        if not root:
            continue
        for subdir in subdirs:
            for executable_name in _PROCESS_NAMES:
                candidates.append(Path(root) / subdir / executable_name)

    for executable_name in _PROCESS_NAMES:
        resolved = shutil.which(executable_name)
        if resolved:
            candidates.append(Path(resolved))

    return candidates


def find_opend_executable() -> Path | None:
    """Find Futu OpenD, preferring the path configured by the user."""
    seen: set[str] = set()
    for candidate in _candidate_paths():
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.is_file():
            return candidate.resolve()
    return None


def ensure_opend_running() -> bool:
    """Start Futu OpenD if needed, without preventing PA Agent startup on failure."""
    if sys.platform != "win32":
        logger.info("Futu OpenD auto-start skipped: Windows is required")
        return False

    if is_opend_running():
        logger.info("Futu OpenD is already running")
        return True

    executable = find_opend_executable()
    if executable is None:
        logger.warning(
            "Futu OpenD was not found; install it or set %s to its executable path",
            OPEND_PATH_ENV,
        )
        return False

    creationflags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    try:
        subprocess.Popen(
            [str(executable)],
            cwd=str(executable.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError as exc:
        logger.warning("Failed to start Futu OpenD from %s: %s", executable, exc)
        return False

    logger.info("Futu OpenD started from %s", executable)
    return True
