import json, os, subprocess, uuid
from pathlib import Path
from qclaw_gateway_token import read_gateway_token
TOKEN = read_gateway_token()
rt = json.loads(Path.home().joinpath(".qclaw","qclaw.json").read_text())
node, mjs = rt["cli"]["nodeBinary"], rt["cli"]["openclawMjs"]
env = {**os.environ, "OPENCLAW_CONFIG_PATH": str(Path.home()/".qclaw/openclaw.json"), "OPENCLAW_STATE_DIR": str(Path.home()/".qclaw")}
script = Path(__file__).with_name("_diag_19000_modelroute.py")
msg = f'/exec host=gateway timeout=60 python "{script.resolve()}"'
params = json.dumps({"sessionKey":"agent:main:main","message":msg,"idempotencyKey":str(uuid.uuid4())})
p = subprocess.run([node,mjs,"gateway","call","chat.send","--token",TOKEN,"--params",params,"--json","--timeout","90000"], env=env, capture_output=True, text=True, timeout=120)
print(p.stdout[-500:])
print(p.stderr[-500:] if p.stderr else "")
