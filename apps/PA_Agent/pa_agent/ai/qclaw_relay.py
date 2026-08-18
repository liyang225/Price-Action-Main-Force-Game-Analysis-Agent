#!/usr/bin/env python3
"""QClaw LLM relay for PA Agent.

Forwards OpenAI-compatible requests to QClaw's internal LLM proxy
(``127.0.0.1:19000/proxy/llm``) while preserving ``reasoning_content``.

Must be started inside the QClaw gateway process tree (via gateway ``/exec``).
"""
from __future__ import annotations

import argparse
import http.server
import json
import logging
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

LISTEN_HOST = "127.0.0.1"
UPSTREAM = "http://127.0.0.1:19000/proxy/llm"
UPSTREAM_URL = urllib.parse.urlparse(UPSTREAM)
LOG = logging.getLogger("pa_agent.qclaw_relay")


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        target = UPSTREAM + self.path
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len > 0 else b""

        incoming_headers: dict[str, str] = {}
        for key, value in self.headers.items():
            low = key.lower()
            if low not in ("connection", "transfer-encoding", "content-length"):
                incoming_headers[key] = value
        incoming_headers["host"] = UPSTREAM_URL.netloc

        req = urllib.request.Request(target, data=body, headers=incoming_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                self.send_response(resp.status)
                skip = {"transfer-encoding", "connection"}
                for key, value in resp.getheaders():
                    if key.lower() not in skip:
                        self.send_header(key, value)
                self.end_headers()
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            LOG.error("upstream HTTP %s: %s", exc.code, detail)
            payload = json.dumps({"error": f"HTTP Error {exc.code}: {detail}"}).encode()
            self.send_response(502)
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            LOG.error("upstream error: %s", exc)
            payload = json.dumps({"error": str(exc)}).encode()
            self.send_response(502)
            self.end_headers()
            self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path in ("/", "/health"):
            payload = json.dumps({"ok": True, "upstream": UPSTREAM, "service": "pa-agent-qclaw-relay"})
            body = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.rstrip("/") == "/v1/models":
            payload = {
                "object": "list",
                "data": [
                    {"id": "pool-deepseek-v4-pro", "object": "model"},
                    {"id": "pool-deepseek-v4-flash", "object": "model"},
                ],
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def _find_free_port(start: int) -> int:
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex((LISTEN_HOST, port)) != 0:
                return port
    return start


def _self_test(port: int, token: str | None) -> None:
    time.sleep(0.5)
    body = json.dumps(
        {
            "model": "pool-deepseek-v4-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 50,
            "stream": False,
        }
    ).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"http://{LISTEN_HOST}:{port}/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read(1000)
            has_reasoning = b"reasoning_content" in raw
            print(f"SELFTEST_OK has_reasoning={has_reasoning} status={resp.status}", flush=True)
    except Exception as exc:
        print(f"SELFTEST_ERROR: {exc}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="PA Agent QClaw reasoning relay")
    parser.add_argument("--port", type=int, default=19004)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--token", default="")
    args = parser.parse_args()

    port = args.port if args.port > 0 else _find_free_port(19004)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex((LISTEN_HOST, port)) == 0:
            print(f"PROXY_ALREADY_RUNNING host={LISTEN_HOST} port={port}", flush=True)
            return 0

    server = http.server.HTTPServer((LISTEN_HOST, port), ProxyHandler)
    print(f"PROXY_STARTED host={LISTEN_HOST} port={port} upstream={UPSTREAM}", flush=True)
    if args.self_test:
        threading.Thread(target=_self_test, args=(port, args.token or None), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
