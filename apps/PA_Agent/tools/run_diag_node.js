import json, os, subprocess, uuid, time
from pathlib import Path
TOKEN = (process.env.QCLAW_GATEWAY_TOKEN || "")
rt = json.loads(Path.home().joinpath(".qclaw", "qclaw.json").read_text())
node, mjs = rt["cli"]["nodeBinary"], rt["cli"]["openclawMjs"]
env = {**os.environ, "OPENCLAW_CONFIG_PATH": str(Path.home() / ".qclaw/openclaw.json"), "OPENCLAW_STATE_DIR": str(Path.home() / ".qclaw")}
script = Path(__file__).with_name("_diag_19000_node.js").resolve()
msg = f'timeout=90 node "{script}"'
params = json.dumps({"sessionKey": "agent:main:main", "message": msg, "idempotencyKey": str(uuid.uuid4())})
subprocess.run([node, mjs, "gateway", "call", "chat.send", "--token", TOKEN, "--params", params, "--json", "--timeout", "120000"], env=env)
time.sleep(35)
p = sorted(Path.home().joinpath(".qclaw/agents/main/sessions").glob("*.jsonl"), key=lambda x: x.stat().st_mtime)[-1]
for line in p.read_text(encoding="utf-8").splitlines()[-8:]:
    d = json.loads(line)
    if d.get("message", {}).get("role") == "toolResult":
        t = "".join(x.get("text", "") for x in d["message"].get("content", []) if isinstance(x, dict))
        if "status" in t or "ERR" in t:
            print(t)
