"""Read QClaw gateway token from local OpenClaw config (never hardcode tokens)."""
from __future__ import annotations

import json
from pathlib import Path


def read_gateway_token() -> str:
    path = Path.home() / ".qclaw" / "openclaw.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    return str(data.get("gateway", {}).get("auth", {}).get("token", "") or "")
