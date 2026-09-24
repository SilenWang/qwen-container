"""llama-server 的极简 HTTP 客户端。

只用标准库：pixi 环境里不必为了发几个请求引入额外依赖。

环境变量（都可在 .env 里配置，脚本会自动读取同目录上一级的 .env）：
    BASE_URL    覆盖完整地址，默认 http://127.0.0.1:$PORT
    PORT        宿主端口，默认 8080
    READY_TIMEOUT  等待就绪的最长秒数，默认 1800
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(path: str | None = None) -> None:
    """把项目根目录的 .env 读进 os.environ（不覆盖已存在的变量）。

    只支持 KEY=VALUE 这种简单形式，够用且不引入 python-dotenv。
    """
    path = path or os.path.join(PROJECT_DIR, ".env")
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
    return os.environ.get("BASE_URL") or f"http://127.0.0.1:{os.environ.get('PORT', '8080')}"


def request(path: str, payload: dict | None = None, timeout: float = 3600) -> dict | None:
    url = base_url().rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body) if body else None


def health() -> dict | None:
    return request("/health", timeout=10)


def wait_ready(timeout: float | None = None, interval: float = 3.0, verbose: bool = True) -> float:
    """轮询 /health 直到就绪，返回等待秒数。

    --load-mode none 下 22GB 权重首次加载可能几分钟，所以默认给 30 分钟。
    """
    load_env()
    timeout = float(timeout or os.environ.get("READY_TIMEOUT", 1800))
    started = time.time()
    last: object = None
    while time.time() - started < timeout:
        try:
            state = health()
            status = (state or {}).get("status")
            # "no slot available" = 服务已就绪但所有 slot 在忙
            if status in ("ok", "no slot available"):
                return time.time() - started
            last = state
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            last = exc
        if verbose:
            print(f"    ... 等待服务就绪（{time.time() - started:.0f}s）", flush=True)
        time.sleep(interval)
    raise TimeoutError(f"等待 {base_url()}/health 就绪超时（{timeout:.0f}s），最后状态：{last}")


def server_info() -> dict:
    """汇总 /props 里的关键参数。"""
    props = request("/props", timeout=30) or {}
    gen = props.get("default_generation_settings") or {}
    return {
        "model_path": props.get("model_path"),
        "n_ctx": props.get("n_ctx") or gen.get("n_ctx"),
        "total_slots": props.get("total_slots"),
        "has_chat_template": bool(props.get("chat_template")),
    }


def count_tokens(text: str) -> int:
    """用服务端 /tokenize 精确计数（避免自己猜 token 数）。"""
    result = request("/tokenize", {"content": text}, timeout=600)
    tokens = (result or {}).get("tokens")
    if tokens is None:
        raise RuntimeError(f"/tokenize 返回异常：{result}")
    return len(tokens)


def chat(prompt: str, max_tokens: int = 64, cache_prompt: bool = False,
         timeout: float = 7200, **extra) -> dict:
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "cache_prompt": cache_prompt,
    }
    payload.update(extra)
    return request("/v1/chat/completions", payload, timeout=timeout) or {}


def completion_text(resp: dict) -> str:
    choices = resp.get("choices") or [{}]
    message = choices[0].get("message") or {}
    return message.get("content") or ""