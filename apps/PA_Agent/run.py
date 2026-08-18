"""直接运行此文件启动 PA Agent。

用法：
    python run.py

或者双击 run.py（如果系统关联了 Python）。

注意：不要在 Spyder / Jupyter 里用 %runfile 启动本程序——会杀死当前内核。
请用系统终端 ``python run.py``，或在 Spyder 中运行本文件（会自动转到独立进程）。
"""
from __future__ import annotations

import os
import subprocess
import sys

# 确保 PA_Agent 目录在 sys.path 里（当从其他目录运行时也能找到包）
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)
os.chdir(_here)


def _inside_ipython_kernel() -> bool:
    """True when executed inside Spyder/Jupyter IPython kernel (not a plain terminal)."""
    if "--subprocess" in sys.argv:
        return False
    if os.environ.get("IPYTHON_KERNEL_APP") or os.environ.get("SPYDER_ARGS"):
        return True
    try:
        from IPython import get_ipython

        shell = get_ipython()
    except Exception:
        return False
    if shell is None:
        return False
    if getattr(shell, "kernel", None) is not None:
        return True
    return shell.__class__.__name__ in ("ZMQInteractiveShell", "SpyderShell")


def _launch_detached_subprocess() -> None:
    """Start PA Agent in a separate process so the IDE kernel stays alive."""
    script = os.path.join(_here, "run.py")
    executable = sys.executable
    if sys.platform == "win32":
        pythonw = os.path.join(os.path.dirname(executable), "pythonw.exe")
        if os.path.isfile(pythonw):
            executable = pythonw
    cmd = [executable, script, "--subprocess"]
    kwargs: dict = {"cwd": _here, "close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    subprocess.Popen(cmd, **kwargs)


def _print_embedded_console_help() -> None:
    msg = (
        "\n"
        "══════════════════════════════════════════════════════════════\n"
        "  PA Agent 是 PyQt6 桌面程序，不能在 Spyder/Jupyter 内核里直接 %runfile。\n"
        "  继续在内核里运行会导致「The kernel died」且通常没有 Python  traceback。\n"
        "\n"
        "  已尝试在独立进程中启动 GUI。若窗口未出现，请在终端执行：\n"
        f"      python \"{os.path.join(_here, 'run.py')}\"\n"
        "\n"
        "  崩溃排查可查看：logs/pa_agent.log 、 logs/crash.log\n"
        "══════════════════════════════════════════════════════════════\n"
    )
    print(msg, flush=True)


from pa_agent.main import main

if __name__ == "__main__":
    if _inside_ipython_kernel():
        _print_embedded_console_help()
        _launch_detached_subprocess()
        raise SystemExit(0)
    raise SystemExit(main())
