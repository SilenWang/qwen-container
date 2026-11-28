#!/usr/bin/env python3
"""scripts/idle_unload.py 的单元测试：只用本机回环，不需要 GPU 或 Strata 服务。

    pixi run test
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "idle_unload.py")
sys.path.insert(0, HERE)

import idle_unload  # noqa: E402 - 单测与被测脚本同目录


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


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        State.status = {}
        State.unloads = 0
        State.auth = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.state_dir = tempfile.mkdtemp(prefix="idle-unload-")
        self.state_file = os.path.join(self.state_dir, "state.json")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    @staticmethod
    def loaded(idle: float, in_flight: int = 0, started_ago: float | None = None) -> dict:
        now = time.time()
        status = {
            "loaded": True,
            "started": int(now - (started_ago if started_ago is not None else idle)),
            "activity": {"in_flight": in_flight, "last_request_at": int(now - idle)},
        }
        return status


class IdleUnloadTest(ServerCase):
    def run_once(self, **env: str) -> subprocess.CompletedProcess[str]:
        merged = os.environ.copy()
        merged.update({
            "STRATA_URL": self.url,
            "STRATA_API_KEY": "test-key",
            "STRATA_IDLE_UNLOAD_S": "1800",
            "STRATA_IDLE_POLL_S": "60",
            "STRATA_IDLE_UNLOAD_COOLDOWN_S": "600",
            "STRATA_IDLE_STATE_FILE": self.state_file,
        })
        merged.update(env)
        return subprocess.run(
            [sys.executable, SCRIPT, "--once"],
            env=merged, capture_output=True, text=True, timeout=10,
        )

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

    def test_same_idle_period_is_unloaded_once_across_restarts(self) -> None:
        # 回归：同一段空闲重复 /unload 会把引擎重载失败叠成 Strata 的 3 次熔断
        State.status = self.loaded(2000)
        first = self.run_once()
        self.assertEqual(State.unloads, 1, first.stdout)
        second = self.run_once()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(State.unloads, 1, second.stdout)
        self.assertIn("不再重复调用", second.stdout)


class RepeatedUnloadTest(ServerCase):
    """进程内调用 check_once，精确控制时间：冷却与「有新请求才再卸」。"""

    def check(self, now: float, state: idle_unload.WatchState, cooldown: float = 600.0) -> tuple[str, str]:
        return idle_unload.check_once(self.url, "test-key", 1800.0, now=now, state=state, cooldown_s=cooldown)

    def test_same_idle_is_not_unloaded_twice(self) -> None:
        now = time.time()
        State.status = self.loaded(2000)
        state = idle_unload.WatchState()
        self.assertEqual(self.check(now, state)[0], "unloaded")
        self.assertEqual(State.unloads, 1)
        again = self.check(now + 60, state)
        self.assertEqual(again[0], "unloaded")
        self.assertEqual(State.unloads, 1)

    def test_new_request_needs_the_cooldown_to_pass(self) -> None:
        now = time.time()
        State.status = self.loaded(2000)
        state = idle_unload.WatchState()
        self.assertEqual(self.check(now, state)[0], "unloaded")
        self.assertEqual(State.unloads, 1)

        # 新请求进来了（last_request_at 前进），又空闲到阈值，但距上次卸载才 130s
        fresh = now - 2000 + 130
        State.status = {"loaded": True, "started": int(now - 2000),
                        "activity": {"in_flight": 0, "last_request_at": int(fresh)}}
        cooling = self.check(now + 130, state)
        self.assertEqual(cooling[0], "cooldown", cooling[1])
        self.assertEqual(State.unloads, 1)

        # 冷却过了，同一段空闲可以再卸一次
        later = self.check(now + 700, state)
        self.assertEqual(later[0], "unloaded", later[1])
        self.assertEqual(State.unloads, 2)

    def test_state_file_round_trip(self) -> None:
        now = time.time()
        State.status = self.loaded(2000)
        state = idle_unload.WatchState()
        self.check(now, state)
        state.save(self.state_file)
        loaded = idle_unload.WatchState.load(self.state_file)
        self.assertAlmostEqual(loaded.last_unload_at, now)
        self.assertIsNotNone(loaded.activity_at)

    def test_strata_restart_forgets_the_previous_idle(self) -> None:
        now = time.time()
        State.status = self.loaded(2000)
        state = idle_unload.WatchState()
        self.check(now, state)
        self.assertEqual(State.unloads, 1)

        # 服务进程重启：started 变新，没有 last_request_at。旧记忆必须失效，否则新进程的空闲永远卸不掉
        State.status = {"loaded": True, "started": int(now + 10 - 9000), "activity": {}}
        result = self.check(now + 10, state)
        self.assertEqual(result[0], "unloaded", result[1])
        self.assertEqual(State.unloads, 2)

    def test_cooldown_can_be_disabled(self) -> None:
        now = time.time()
        State.status = self.loaded(2000)
        state = idle_unload.WatchState()
        self.check(now, state)
        fresh = now - 2000 + 120
        State.status = {"loaded": True, "started": int(now - 2000),
                        "activity": {"in_flight": 0, "last_request_at": int(fresh)}}
        result = self.check(now + 120, state, cooldown=0)
        self.assertEqual(result[0], "unloaded", result[1])
        self.assertEqual(State.unloads, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
