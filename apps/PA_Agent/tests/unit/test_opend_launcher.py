"""Tests for Futu OpenD startup handling."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pa_agent.util.opend_launcher as launcher


def test_is_opend_running_recognizes_installed_process_name(monkeypatch) -> None:
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='"Futu_OpenD.exe","38372","Console","4","116,504 K"',
        ),
    )

    assert launcher.is_opend_running()


def test_ensure_does_not_start_a_second_process(monkeypatch) -> None:
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "is_opend_running", lambda: True)
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not launch")),
    )

    assert launcher.ensure_opend_running()


def test_ensure_starts_discovered_executable(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "Futu_OpenD.exe"
    executable.touch()
    calls: list[tuple[list[str], dict]] = []

    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "is_opend_running", lambda: False)
    monkeypatch.setattr(launcher, "find_opend_executable", lambda: executable)
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    assert launcher.ensure_opend_running()
    assert calls[0][0] == [str(executable)]
    assert calls[0][1]["cwd"] == str(tmp_path)


def test_environment_path_has_priority(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "custom-opend.exe"
    executable.touch()
    monkeypatch.setenv(launcher.OPEND_PATH_ENV, str(executable))
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)

    assert launcher.find_opend_executable() == executable.resolve()


def test_missing_executable_does_not_raise(monkeypatch) -> None:
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "is_opend_running", lambda: False)
    monkeypatch.setattr(launcher, "find_opend_executable", lambda: None)

    assert not launcher.ensure_opend_running()


def test_launch_failure_does_not_raise(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "Futu_OpenD.exe"
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "is_opend_running", lambda: False)
    monkeypatch.setattr(launcher, "find_opend_executable", lambda: executable)
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("denied")),
    )

    assert not launcher.ensure_opend_running()


def test_non_windows_platform_is_skipped(monkeypatch) -> None:
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(
        launcher,
        "is_opend_running",
        lambda: (_ for _ in ()).throw(AssertionError("must not inspect")),
    )

    assert not launcher.ensure_opend_running()
