from __future__ import annotations

import os
import run


def test_detached_windows_launch_prefers_pythonw(monkeypatch) -> None:
    launched: dict[str, object] = {}
    executable = r"C:\Python314\python.exe"

    monkeypatch.setattr(run.sys, "platform", "win32")
    monkeypatch.setattr(run.sys, "executable", executable)
    monkeypatch.setattr(run.os.path, "isfile", lambda path: path.endswith("pythonw.exe"))
    monkeypatch.setattr(
        run.subprocess,
        "Popen",
        lambda command, **kwargs: launched.update(command=command, kwargs=kwargs),
    )

    run._launch_detached_subprocess()

    assert launched["command"][0] == os.path.join(r"C:\Python314", "pythonw.exe")
    creationflags = launched["kwargs"]["creationflags"]
    assert creationflags & run.subprocess.CREATE_NEW_PROCESS_GROUP
    assert creationflags & run.subprocess.CREATE_NO_WINDOW
