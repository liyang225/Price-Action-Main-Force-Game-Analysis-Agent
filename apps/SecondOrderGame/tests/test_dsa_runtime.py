from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from src.integration.dsa_runtime import ensure_current_dsa_market_review


def test_dsa_runtime_reuses_same_date_review_without_starting_process(tmp_path) -> None:
    calls: list[object] = []

    result = ensure_current_dsa_market_review(
        {"dsa_database_path": str(tmp_path)},
        now=datetime(2026, 8, 13, 11, 30),
        loader=lambda *_args, **_kwargs: {
            "status": "ready",
            "created_at": "2026-08-13T10:15:00",
            "data_date": "2026-08-13",
        },
        command_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert result["triggered"] is False
    assert calls == []


def test_dsa_runtime_regenerates_when_entering_afternoon_window(tmp_path) -> None:
    executable = tmp_path / "stock_analysis.exe"
    executable.write_bytes(b"test")
    install = tmp_path / "desktop"
    install.mkdir()
    states = iter(
        [
            {
                "status": "ready",
                "created_at": "2026-08-13T11:30:00",
                "data_date": "2026-08-13",
            },
            {
                "status": "ready",
                "created_at": "2026-08-13T15:05:00",
                "data_date": "2026-08-13",
            },
        ]
    )
    calls: list[object] = []

    result = ensure_current_dsa_market_review(
        {"dsa_database_path": str(tmp_path)},
        now=datetime(2026, 8, 13, 15, 0),
        executable=executable,
        install_root=install,
        loader=lambda *_args, **_kwargs: next(states),
        command_runner=lambda *args, **kwargs: (
            calls.append((args, kwargs))
            or SimpleNamespace(returncode=0, stdout="ok", stderr="")
        ),
    )

    assert result["triggered"] is True
    assert len(calls) == 1


def test_dsa_runtime_force_regenerates_within_same_half_day(tmp_path) -> None:
    executable = tmp_path / "stock_analysis.exe"
    executable.write_bytes(b"test")
    install = tmp_path / "desktop"
    install.mkdir()
    states = iter(
        [
            {
                "status": "ready",
                "created_at": "2026-08-13T10:00:00",
                "data_date": "2026-08-13",
            },
            {
                "status": "ready",
                "created_at": "2026-08-13T10:31:00",
                "data_date": "2026-08-13",
            },
        ]
    )
    calls: list[object] = []

    result = ensure_current_dsa_market_review(
        {"dsa_database_path": str(tmp_path)},
        now=datetime(2026, 8, 13, 10, 30),
        executable=executable,
        install_root=install,
        loader=lambda *_args, **_kwargs: next(states),
        command_runner=lambda *args, **kwargs: (
            calls.append((args, kwargs))
            or SimpleNamespace(returncode=0, stdout="ok", stderr="")
        ),
        force=True,
    )

    assert result["triggered"] is True
    assert len(calls) == 1


def test_dsa_runtime_invokes_packaged_backend_and_reloads_database(tmp_path) -> None:
    executable = tmp_path / "stock_analysis.exe"
    executable.write_bytes(b"test")
    install = tmp_path / "desktop"
    install.mkdir()
    (install / ".env").write_text("MARKET_REVIEW_ENABLED=true", encoding="utf-8")
    states = iter(
        [
            {"status": "stale", "reason": "old"},
            {"status": "ready", "data_date": "2026-08-13"},
        ]
    )
    calls: list[tuple[object, object]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    result = ensure_current_dsa_market_review(
        {"dsa_database_path": str(tmp_path / "data")},
        now=datetime(2026, 8, 13, 11, 30),
        executable=executable,
        install_root=install,
        loader=lambda *_args, **_kwargs: next(states),
        command_runner=run,
    )

    assert result["triggered"] is True
    command, kwargs = calls[0]
    assert command[1:] == ["--market-review", "--no-notify", "--force-run"]
    assert kwargs["env"]["ENV_FILE"] == str(install / ".env")
    assert kwargs["env"]["DATABASE_PATH"].endswith("data\\stock_analysis.db")
