#!/usr/bin/env python3
"""Strata 服务的 OpenAI 兼容最小客户端，smoke.py / longctx.py 共用。

只用标准库。配置从项目根目录的 .env 读（不覆盖已有环境变量）；
API key 优先取 STRATA_API_KEY，留空则从容器里的 /data/state/api-key 读。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env() -> None:
    """把项目根目录的 .env 读进 os.environ（不覆盖已存在的变量）。"""
    path = os.path.join(PROJECT_DIR, ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def base_url() -> str:
    host = os.environ.get("STRATA_BIND_ADDR", "127.0.0.1")
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"http://{host}:{os.environ.get('STRATA_PORT', '8080')}"


def api_key() -> str:
    key = os.environ.get("STRATA_API_KEY", "").strip()
    if key:
        return key
    # 首启自动生成的 key 存在容器的 /data/state/api-key
    try:
        out = subprocess.run(
            ["docker", "compose", "exec", "-T", "strata", "cat", "/data/state/api-key"],
            cwd=PROJECT_DIR, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def request(path: str, key: str = "", payload: dict | None = None, timeout: float = 600):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base_url() + path, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else {}


def wait_ready(timeout: float | None = None) -> float:
    """等 /health 返回 200。首次启动要下载模型，可能很久（默认 1 小时，可用 STRATA_READY_TIMEOUT 调）。"""
    if timeout is None:
        timeout = float(os.environ.get("STRATA_READY_TIMEOUT", "3600"))
    started = time.time()
    last = ""
    last_note = 0.0
    while time.time() < started + timeout:
        try:
            request("/health", timeout=5)
            return time.time() - started
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - 连接被拒 / 超时都只是「还没起来」
            last = str(exc)
        waited = time.time() - started
        if waited - last_note >= 30:
            last_note = waited
            print(f"      等待中（{waited:.0f}s）：{last}")
            print("      首次启动会下载模型，进度看 `pixi run logs`")
        time.sleep(2)
    raise SystemExit(f"服务在 {timeout:.0f}s 内没有就绪（最后一次：{last}）")


def model_ids(key: str) -> list[str]:
    models = (request("/v1/models", key) or {}).get("data") or []
    return [m.get("id") for m in models if isinstance(m, dict) and m.get("id")]


def chat(prompt: str, max_tokens: int = 256, key: str = "", model: str = "strata",
         timeout: float = 600, disable_thinking: bool = True) -> dict:
    """发一次 chat 补全。

    Flash-Next / 35B-A3B 默认会先输出思考段：思考会把 max_tokens 吃光，正文（content）
    变成空字符串，短回复的脚本就会误判失败。所以默认用 chat_template_kwargs 关掉思考，
    短 max_tokens 也能直接拿到正文。确实需要思考段时传 disable_thinking=False。
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": False,
    }
    if disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return request("/v1/chat/completions", key, payload, timeout=timeout)


def completion_text(resp: dict) -> str:
    choices = resp.get("choices") or [{}]
    msg = choices[0].get("message") or {}
    return (msg.get("content") or "").strip()


def main() -> int:  # 直接运行本文件时给个连通性检查，方便排查
    load_env()
    print(f"base_url = {base_url()}")
    key = api_key()
    print(f"api_key  = {'已取得' if key else '没拿到（设 STRATA_API_KEY 或先启动容器）'}")
    return 0 if key else 1


if __name__ == "__main__":
    sys.exit(main())
