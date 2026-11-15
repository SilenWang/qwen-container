#!/usr/bin/env python3
"""空闲释放看门狗：距最后一次请求超过 N 秒就调用 Strata 的 POST /unload。

主服务由 `strata launch` 启动，它的 serve.server 支持 `--idle-unload`，但 launch
没有透传这个开关。这个脚本用 /v1/status 判断空闲，用 /unload 卸载模型；下一次
请求由 Strata 自动重新加载。容器里由 `idle-unload` 服务运行，只用标准库。

环境变量：
  STRATA_URL              服务地址，默认 http://strata:8080
  STRATA_IDLE_UNLOAD_S    空闲多少秒后卸载，默认 1800；<= 0 关闭
  STRATA_IDLE_POLL_S      检查间隔，默认 60
  STRATA_API_KEY          优先使用；留空则读 STRATA_KEY_FILE
  STRATA_KEY_FILE         API key 文件，默认 /data/state/api-key
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


def idle_seconds(status: dict, now: float | None = None) -> float | None:
    activity = status.get("activity") or {}
    last = activity.get("last_request_at") or status.get("started")
    if not last:
        return None
    return max(0.0, (now if now is not None else time.time()) - float(last))


def check_once(url: str, key: str, idle_s: float, now: float | None = None) -> tuple[str, str]:
    """检查一次；返回 (状态, 说明)。状态为 unloaded / busy / idle / not-loaded / no-key / error。"""
    if not key:
        return "no-key", "没有 API key：检查 .env 的 STRATA_API_KEY 或 /data/state/api-key"
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
    activity = status.get("activity") or {}
    in_flight = int(activity.get("in_flight") or 0)
    if in_flight:
        return "busy", f"{in_flight} 个请求在跑"
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
    return "unloaded", f"空闲 {idle:.0f}s >= {idle_s:.0f}s；模型已卸载（HTTP {code}），下次请求会自动加载"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="空闲时卸载 Strata 模型的看门狗")
    parser.add_argument("--once", action="store_true", help="只检查一次就退出（测试用）")
    args = parser.parse_args(argv)

    url = os.environ.get("STRATA_URL", DEFAULT_URL).rstrip("/")
    idle_s = _number("STRATA_IDLE_UNLOAD_S", DEFAULT_IDLE_S)
    poll_s = max(5.0, _number("STRATA_IDLE_POLL_S", DEFAULT_POLL_S))

    if idle_s <= 0:
        print("[idle-unload] 已关闭（STRATA_IDLE_UNLOAD_S <= 0）", flush=True)
        return 0

    if args.once:
        state, detail = check_once(url, api_key(), idle_s)
        print(f"[idle-unload] {state}: {detail}", flush=True)
        return 0 if state != "error" else 1

    print(f"[idle-unload] 监视 {url}：空闲 {idle_s:.0f}s 后卸载，每 {poll_s:.0f}s 检查一次", flush=True)
    last_note = 0.0
    while True:
        state, detail = check_once(url, api_key(), idle_s)
        if state == "unloaded":
            print(f"[idle-unload] {detail}", flush=True)
        elif state in ("error", "no-key") and time.time() - last_note >= 300:
            print(f"[idle-unload] {state}: {detail}", flush=True)
            last_note = time.time()
        time.sleep(poll_s)


if __name__ == "__main__":
    raise SystemExit(main())
