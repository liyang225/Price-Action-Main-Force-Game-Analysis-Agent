"""Run auth diag via gateway exec (default, not host=gateway)."""
import json, os, subprocess, uuid
from pathlib import Path
from qclaw_gateway_token import read_gateway_token

TOKEN = read_gateway_token()
rt = json.loads(Path.home().joinpath(".qclaw", "qclaw.json").read_text())
node, mjs = rt["cli"]["nodeBinary"], rt["cli"]["openclawMjs"]
env = {
    **os.environ,
    "OPENCLAW_CONFIG_PATH": str(Path.home() / ".qclaw/openclaw.json"),
    "OPENCLAW_STATE_DIR": str(Path.home() / ".qclaw"),
}
script = Path(__file__).with_name("_diag_19000_auth.py").resolve()

for host in ["gateway", ""]:
    flag = f" host={host}" if host else ""
    msg = f'/exec{flag} timeout=60 python "{script}"'
    params = json.dumps({"sessionKey": "agent:main:main", "message": msg, "idempotencyKey": str(uuid.uuid4())})
    print(f"=== exec{flag or ' (default)'} ===")
    p = subprocess.run(
        [node, mjs, "gateway", "call", "chat.send", "--token", TOKEN, "--params", params, "--json", "--timeout", "90000"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    print(p.stdout[-400:])
    print(p.stderr[-200:] if p.stderr else "")
