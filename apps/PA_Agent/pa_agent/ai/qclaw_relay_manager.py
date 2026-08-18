"""Start and monitor the PA Agent QClaw reasoning relay."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_RELAY_SCRIPT = Path(__file__).with_name("qclaw_relay.py")
_QCLAW_RUNTIME_PATH = Path.home() / ".qclaw" / "qclaw.json"
_QCLAW_CONFIG_PATH = Path.home() / ".qclaw" / "openclaw.json"
_DEFAULT_RELAY_PORT = 19004
_STARTUP_TIMEOUT_S = 20.0

_spawn_lock = threading.Lock()


def _read_qclaw_runtime() -> dict | None:
    if not _QCLAW_RUNTIME_PATH.exists():
        return None
    try:
        return json.loads(_QCLAW_RUNTIME_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Failed to read QClaw runtime %s: %s", _QCLAW_RUNTIME_PATH, exc)
        return None


def _openclaw_cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["OPENCLAW_CONFIG_PATH"] = str(_QCLAW_CONFIG_PATH)
    env["OPENCLAW_STATE_DIR"] = str(Path.home() / ".qclaw")
    return env


def _openclaw_cli(runtime: dict) -> tuple[str, str] | None:
    cli = runtime.get("cli") or {}
    node = cli.get("nodeBinary")
    mjs = cli.get("openclawMjs")
    if node and mjs and Path(node).exists() and Path(mjs).exists():
        return str(node), str(mjs)
    return None


def _probe_relay_health(port: int, *, timeout: float = 1.5) -> bool:
    try:
        import httpx

        resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=timeout)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return bool(data.get("ok"))
    except Exception:
        return False


def _minimal_upstream_payload() -> dict:
    """Payload shape that QClaw's internal proxy accepted in prior tests."""
    return {
        "model": "pool-deepseek-v4-flash",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 50,
        "stream": False,
    }


def _verify_relay_upstream(port: int, token: str, *, timeout: float = 25.0) -> tuple[bool, str]:
    """Return (ok, detail) after a minimal relay → 19000 chat call."""
    try:
        import httpx

        resp = httpx.post(
            f"http://127.0.0.1:{port}/chat/completions",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=_minimal_upstream_payload(),
            timeout=timeout,
        )
        if resp.status_code == 200:
            has_reasoning = "reasoning_content" in resp.text
            return True, f"upstream OK (reasoning={'yes' if has_reasoning else 'no'})"
        detail = resp.text[:300]
        logger.warning("Relay upstream verification HTTP %s: %s", resp.status_code, detail)
        return False, f"HTTP {resp.status_code}: {detail}"
    except Exception as exc:
        logger.debug("Relay upstream verification failed: %s", exc)
        return False, str(exc)


def _start_relay_via_openclaw_gateway(token: str, port: int) -> bool:
    """Ask QClaw agent to run the relay via native ``/exec`` (inherits QCLAW env)."""
    runtime = _read_qclaw_runtime()
    if runtime is None:
        return False
    cli = _openclaw_cli(runtime)
    if cli is None:
        logger.warning("OpenClaw CLI paths not found in %s", _QCLAW_RUNTIME_PATH)
        return False

    node, mjs = cli
    relay_script = _RELAY_SCRIPT.resolve()
    message = (
        f'/exec background=true yieldMs=600000 '
        f'python "{relay_script}" --port {port} --self-test --token {token}'
    )
    params = json.dumps(
        {
            "sessionKey": "agent:main:main",
            "message": message,
            "idempotencyKey": str(uuid.uuid4()),
        }
    )
    try:
        proc = subprocess.run(
            [
                node,
                mjs,
                "gateway",
                "call",
                "chat.send",
                "--token",
                token,
                "--params",
                params,
                "--json",
                "--timeout",
                "30000",
            ],
            env=_openclaw_cli_env(),
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning(
                "openclaw chat.send /exec failed (code=%s): %s",
                proc.returncode,
                (proc.stderr or proc.stdout)[-500:],
            )
            return False
        logger.info("Requested QClaw gateway /exec to start relay on port %s", port)
        return True
    except Exception as exc:
        logger.warning("Failed to request QClaw relay start: %s", exc)
        return False


def _wait_for_relay(port: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _probe_relay_health(port):
            return True
        time.sleep(0.5)
    return False


def ensure_qclaw_relay(token: str, *, port: int = _DEFAULT_RELAY_PORT) -> tuple[bool, str]:
    """Ensure the reasoning relay is reachable and upstream works.

    Returns ``(ok, message)``.
    """
    with _spawn_lock:
        if _probe_relay_health(port):
            upstream_ok, upstream_detail = _verify_relay_upstream(port, token)
            if upstream_ok:
                return True, f"QClaw 中继代理已运行 (127.0.0.1:{port})，支持 reasoning 流"
            logger.warning(
                "Relay on port %s is up but upstream check failed (%s); restarting",
                port,
                upstream_detail,
            )

        if not _start_relay_via_openclaw_gateway(token, port):
            return (
                False,
                "无法通过 QClaw Gateway 启动 reasoning 中继代理。"
                "请确认 QClaw 正在运行。",
            )

        if not _wait_for_relay(port, _STARTUP_TIMEOUT_S):
            return (
                False,
                "已请求 QClaw 启动中继代理，但端口未在预期时间内就绪。",
            )

        upstream_ok, upstream_detail = _verify_relay_upstream(port, token)
        if not upstream_ok:
            return (
                False,
                "中继代理已监听，但 QClaw 内部 LLM 代理 (19000) 拒绝请求："
                f"{upstream_detail}。"
                "请完全重启 QClaw 后重试；将回退到公开网关（无 reasoning 流）。",
            )

        return True, f"QClaw 中继代理已启动 (127.0.0.1:{port})，支持 reasoning 流式输出"
