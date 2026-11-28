#!/usr/bin/env python3
"""空闲释放看门狗：距最后一次请求超过 N 秒就调用 Strata 的 POST /unload。

主服务由 `strata launch` 启动，它的 serve.server 支持 `--idle-unload`，但 launch
没有透传这个开关。这个脚本用 /v1/status 判断空闲，用 /unload 卸载模型；下一次
请求由 Strata 自动重新加载。容器里由 `idle-unload` 服务运行，只用标准库。

一次空闲只卸载一次：`/unload` 成功后会记住当时的 last_request_at，在观测到新请求
（last_request_at 前进）之前不再发 `/unload`，并另有一段冷却期。Strata 0.2.21 起
引擎在 10 分钟内死亡 3 次就熔断（返回 503 "The engine stopped 3 times in 10
minutes"）；重复卸载或者卸载后刚加载就再卸载，会把重载失败叠成熔断，而这两种情况
在日志里很难分辨。状态存进 /tmp，看门狗自己被 docker 重启后也不会立刻再卸一次。

环境变量：
  STRATA_URL                     服务地址，默认 http://strata:8080
  STRATA_IDLE_UNLOAD_S           空闲多少秒后卸载，默认 1800；<= 0 关闭
  STRATA_IDLE_POLL_S             检查间隔，默认 60
  STRATA_IDLE_UNLOAD_COOLDOWN_S  两次卸载的最小间隔，默认 600（Strata 的熔断窗口）；<= 0 关闭冷却
  STRATA_IDLE_STATE_FILE         记住上次卸载的文件，默认 /tmp/idle-unload-state.json
  STRATA_API_KEY                 优先使用；留空则读 STRATA_KEY_FILE
  STRATA_KEY_FILE                API key 文件，默认 /data/state/api-key
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://strata:8080"
DEFAULT_IDLE_S = 1800.0
DEFAULT_POLL_S = 60.0
DEFAULT_COOLDOWN_S = 600.0
DEFAULT_STATE_FILE = "/tmp/idle-unload-state.json"
DEFAULT_KEY_FILE = "/data/state/api-key"


def _number(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise SystemExit(f"{name} 必须是数字，当前是 {raw!r}") from None


def api_key() -> str:
    key = os.environ.get("STRATA_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(os.environ.get("STRATA_KEY_FILE", DEFAULT_KEY_FILE), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _request(url: str, key: str, method: str = "GET", payload: dict | None = None,
             timeout: float = 10.0) -> tuple[int, bytes]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if key:
        req.add_header("Authorization", "Bearer " + key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _as_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def idle_seconds(status: dict, now: float | None = None) -> float | None:
    activity = status.get("activity") or {}
    last = activity.get("last_request_at") or status.get("started")
    if not last:
        return None
    return max(0.0, (now if now is not None else time.time()) - float(last))


class WatchState:
    """跨轮询（以及看门狗重启）记住「已经为这段空闲卸载过」。"""

    def __init__(self, last_unload_at: float = 0.0, activity_at: float | None = None,
                 server_started: float | None = None) -> None:
        self.last_unload_at = last_unload_at
        self.activity_at = activity_at
        self.server_started = server_started

    @classmethod
    def load(cls, path: str | None) -> "WatchState":
        if not path:
            return cls()
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls(
            last_unload_at=_as_float(data.get("last_unload_at")) or 0.0,
            activity_at=_as_float(data.get("activity_at")),
            server_started=_as_float(data.get("server_started")),
        )

    def save(self, path: str | None) -> None:
        if not path:
            return
        payload = {
            "last_unload_at": self.last_unload_at,
            "activity_at": self.activity_at,
            "server_started": self.server_started,
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
        except OSError:
            pass  # 记不住就退回进程内保护，卸载本身不受影响


def check_once(url: str, key: str, idle_s: float, now: float | None = None,
               state: WatchState | None = None, cooldown_s: float = DEFAULT_COOLDOWN_S) -> tuple[str, str]:
    """检查一次；返回 (状态, 说明)。

    状态为 unloaded / busy / idle / cooldown / not-loaded / no-key / error。
    `state` 给了就原地更新：一次空闲只卸载一次，两次卸载之间还有冷却期。
    """
    if not key:
        return "no-key", "没有 API key：检查 .env 的 STRATA_API_KEY 或 /data/state/api-key"
    if state is None:
        state = WatchState()
    now = time.time() if now is None else now
    try:
        _, raw = _request(url + "/v1/status", key)
        status = json.loads(raw or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return "error", "GET /v1/status: HTTP 401（API key 不对）"
        return "error", f"GET /v1/status: HTTP {exc.code}"
    except (OSError, ValueError) as exc:
        return "error", f"GET /v1/status: {exc}"

    if not status.get("loaded"):
        return "not-loaded", "模型已经卸载，不需要处理"

    # 服务重启过（strata 进程换了）：上一段空闲的记忆失效，否则会把新进程的第一段空闲当成旧的。
    started = _as_float(status.get("started"))
    if started is not None:
        if state.server_started is not None and started != state.server_started:
            state.last_unload_at = 0.0
            state.activity_at = None
        state.server_started = started

    activity = status.get("activity") or {}
    in_flight = int(activity.get("in_flight") or 0)
    if in_flight:
        return "busy", f"{in_flight} 个请求在跑"

    stamp = _as_float(activity.get("last_request_at"))
    if state.last_unload_at and stamp is not None and state.activity_at is not None \
            and stamp <= state.activity_at:
        return "unloaded", "上次卸载后还没有新请求：不再重复调用 /unload（重复停止会触发 Strata 的熔断）"

    if state.last_unload_at and cooldown_s > 0:
        left = cooldown_s - (now - state.last_unload_at)
        if left > 0:
            return "cooldown", f"距上次卸载 {now - state.last_unload_at:.0f}s < {cooldown_s:.0f}s，先不动（等重载稳定）"

    idle = idle_seconds(status, now)
    if idle is None or idle < idle_s:
        return "idle", f"空闲 {idle or 0:.0f}s < {idle_s:.0f}s"

    try:
        code, _ = _request(url + "/unload", key, method="POST", payload={}, timeout=30.0)
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return "busy", "POST /unload: 409（有请求在跑）"
        if exc.code == 401:
            return "error", "POST /unload: HTTP 401（API key 不对）"
        return "error", f"POST /unload: HTTP {exc.code}"
    except OSError as exc:
        return "error", f"POST /unload: {exc}"
    state.last_unload_at = now
    state.activity_at = stamp
    return "unloaded", f"空闲 {idle:.0f}s >= {idle_s:.0f}s；模型已卸载（HTTP {code}），下次请求会自动加载"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="空闲时卸载 Strata 模型的看门狗")
    parser.add_argument("--once", action="store_true", help="只检查一次就退出（测试用）")
    args = parser.parse_args(argv)

    url = os.environ.get("STRATA_URL", DEFAULT_URL).rstrip("/")
    idle_s = _number("STRATA_IDLE_UNLOAD_S", DEFAULT_IDLE_S)
    poll_s = max(5.0, _number("STRATA_IDLE_POLL_S", DEFAULT_POLL_S))
    cooldown_s = _number("STRATA_IDLE_UNLOAD_COOLDOWN_S", DEFAULT_COOLDOWN_S)
    state_file = os.environ.get("STRATA_IDLE_STATE_FILE", DEFAULT_STATE_FILE)

    if idle_s <= 0:
        print("[idle-unload] 已关闭（STRATA_IDLE_UNLOAD_S <= 0）", flush=True)
        return 0

    state = WatchState.load(state_file)
    if args.once:
        result, detail = check_once(url, api_key(), idle_s, state=state, cooldown_s=cooldown_s)
        state.save(state_file)
        print(f"[idle-unload] {result}: {detail}", flush=True)
        return 0 if result != "error" else 1

    print(f"[idle-unload] 监视 {url}：空闲 {idle_s:.0f}s 后卸载，每 {poll_s:.0f}s 检查一次"
          + (f"，两次卸载至少隔 {cooldown_s:.0f}s" if cooldown_s > 0 else ""), flush=True)
    previous: str | None = None
    last_note = 0.0
    while True:
        before = state.last_unload_at
        result, detail = check_once(url, api_key(), idle_s, state=state, cooldown_s=cooldown_s)
        if state.last_unload_at != before:
            state.save(state_file)
            print(f"[idle-unload] {detail}", flush=True)
        elif result != previous:                    # 状态变化才说，避免每分钟刷同一行
            print(f"[idle-unload] {result}: {detail}", flush=True)
            last_note = time.time()
        elif result in ("error", "no-key") and time.time() - last_note >= 300:
            print(f"[idle-unload] {result}: {detail}", flush=True)
            last_note = time.time()
        previous = result
        time.sleep(poll_s)


if __name__ == "__main__":
    raise SystemExit(main())
