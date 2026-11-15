#!/usr/bin/env python3
"""scripts/idle_unload.py 的单元测试：只用本机回环，不需要 GPU 或 Strata 服务。

    pixi run test
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "idle_unload.py")


class State:
    status: dict = {}
    unloads: int = 0
    auth: list[str | None] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # noqa: D401 - quiet test server
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        State.auth.append(self.headers.get("Authorization"))
        if self.path == "/v1/status":
            self._send(200, State.status)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        State.auth.append(self.headers.get("Authorization"))
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.path == "/unload":
            State.unloads += 1
            self._send(200, {"status": "unloaded"})
        else:
            self._send(404, {"error": "not found"})


class IdleUnloadTest(unittest.TestCase):
    def setUp(self) -> None:
        State.status = {}
        State.unloads = 0
        State.auth = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def run_once(self, **env: str) -> subprocess.CompletedProcess[str]:
        merged = os.environ.copy()
        merged.update({
            "STRATA_URL": self.url,
            "STRATA_API_KEY": "test-key",
            "STRATA_IDLE_UNLOAD_S": "1800",
            "STRATA_IDLE_POLL_S": "60",
        })
        merged.update(env)
        return subprocess.run(
            [sys.executable, SCRIPT, "--once"],
            env=merged, capture_output=True, text=True, timeout=10,
        )

    def loaded(self, idle: float, in_flight: int = 0) -> dict:
        now = time.time()
        return {
            "loaded": True,
            "started": int(now - idle),
            "activity": {"in_flight": in_flight, "last_request_at": int(now - idle)},
        }

    def test_idle_model_is_unloaded(self) -> None:
        State.status = self.loaded(2000)
        result = self.run_once()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(State.unloads, 1)
        self.assertIn("unloaded", result.stdout)
        self.assertEqual(State.auth[0], "Bearer test-key")

    def test_recent_request_is_kept(self) -> None:
        State.status = self.loaded(10)
        result = self.run_once()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(State.unloads, 0)
        self.assertIn("idle", result.stdout)

    def test_busy_model_is_kept(self) -> None:
        State.status = self.loaded(9999, in_flight=1)
        result = self.run_once()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(State.unloads, 0)
        self.assertIn("busy", result.stdout)

    def test_already_unloaded_is_a_noop(self) -> None:
        State.status = {"loaded": False, "started": int(time.time()) - 9999, "activity": {}}
        result = self.run_once()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(State.unloads, 0)
        self.assertIn("not-loaded", result.stdout)

    def test_disabled_by_zero(self) -> None:
        result = self.run_once(STRATA_IDLE_UNLOAD_S="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(State.unloads, 0)
        self.assertIn("已关闭", result.stdout)

    def test_key_file_is_used_when_env_is_empty(self) -> None:
        State.status = self.loaded(2000)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
            fh.write("file-key\n")
            key_file = fh.name
        try:
            result = self.run_once(STRATA_API_KEY="", STRATA_KEY_FILE=key_file)
        finally:
            os.unlink(key_file)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(State.unloads, 1)
        self.assertEqual(State.auth[0], "Bearer file-key")


if __name__ == "__main__":
    unittest.main(verbosity=2)
