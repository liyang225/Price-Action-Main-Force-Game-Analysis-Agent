"""Run 19000 payload tests inside QClaw gateway via chat.send /exec."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from qclaw_gateway_token import read_gateway_token

TOKEN = read_gateway_token()
CONFIG = Path.home() / ".qclaw" / "openclaw.json"
RUNTIME = Path.home() / ".qclaw" / "qclaw.json"
TEST_SCRIPT = Path(__file__).with_name("_diag_19000_gateway.py")


def main() -> int:
    runtime = json.loads(RUNTIME.read_text(encoding="utf-8"))
    cli = runtime["cli"]
    node, mjs = cli["nodeBinary"], cli["openclawMjs"]
    env = {
        **os.environ,
        "OPENCLAW_CONFIG_PATH": str(CONFIG),
        "OPENCLAW_STATE_DIR": str(Path.home() / ".qclaw"),
    }
    message = (
        f'/exec host=gateway timeout=60 python "{TEST_SCRIPT.resolve()}"'
    )
    params = json.dumps(
        {
            "sessionKey": "agent:main:main",
            "message": message,
            "idempotencyKey": str(uuid.uuid4()),
        }
    )
    proc = subprocess.run(
        [node, mjs, "gateway", "call", "chat.send", "--token", TOKEN, "--params", params, "--json", "--timeout", "90000"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    print("exit", proc.returncode)
    print(proc.stdout[-4000:])
    if proc.stderr:
        print("stderr", proc.stderr[-2000:])
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
