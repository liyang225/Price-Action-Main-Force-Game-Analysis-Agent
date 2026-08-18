import json, os, subprocess, uuid, time
from pathlib import Path
from qclaw_gateway_token import read_gateway_token
TOKEN = read_gateway_token()
rt = json.loads(Path.home().joinpath(".qclaw", "qclaw.json").read_text())
node, mjs = rt["cli"]["nodeBinary"], rt["cli"]["openclawMjs"]
env = {**os.environ, "OPENCLAW_CONFIG_PATH": str(Path.home() / ".qclaw/openclaw.json"), "OPENCLAW_STATE_DIR": str(Path.home() / ".qclaw")}
script = Path(__file__).with_name("_diag_19000_openai_sdk.py").resolve()
msg = f'timeout=90 python "{script}"'
params = json.dumps({"sessionKey": "agent:main:main", "message": msg, "idempotencyKey": str(uuid.uuid4())})
subprocess.run([node, mjs, "gateway", "call", "chat.send", "--token", TOKEN, "--params", params, "--json", "--timeout", "120000"], env=env)
time.sleep(40)
p = sorted(Path.home().joinpath(".qclaw/agents/main/sessions").glob("*.jsonl"), key=lambda x: x.stat().st_mtime)[-1]
for line in p.read_text(encoding="utf-8").splitlines()[-10:]:
    d = json.loads(line)
    if d.get("message", {}).get("role") == "toolResult":
        t = "".join(x.get("text", "") for x in d["message"].get("content", []) if isinstance(x, dict))
        if "OK" in t or "FAIL" in t:
            print(t)
