#!/usr/bin/env python3
"""模型二（Qwen3.8-Flash-Next / Strata）冒烟自检：就绪 → 模型列表 → 一次 chat 补全 → 吞吐。

    pixi run strata-smoke

只用标准库。API key 优先取 .env 里的 STRATA_API_KEY，留空则从容器里的
/data/state/api-key 读取（首启时由 Strata 自动生成）。

退出码非 0 表示自检失败（供 CI / 脚本判断）。
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

PROMPT = os.environ.get("STRATA_SMOKE_PROMPT", "用一句话解释 MoE（混合专家）模型为什么省显存。")
MAX_TOKENS = int(os.environ.get("STRATA_SMOKE_MAX_TOKENS", "96"))
READY_TIMEOUT = int(os.environ.get("STRATA_READY_TIMEOUT", "900"))


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
    return f"http://{host}:{os.environ.get('STRATA_PORT', '8081')}"


def api_key() -> str:
    key = os.environ.get("STRATA_API_KEY", "").strip()
    if key:
        return key
    # 首启自动生成的 key 存在容器的 /data/state/api-key（见镜像的 entrypoint）
    try:
        out = subprocess.run(
            ["docker", "compose", "--profile", "strata", "exec", "-T", "strata", "cat", "/data/state/api-key"],
            cwd=PROJECT_DIR, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def request(path: str, key: str, payload: dict | None = None, timeout: float = 600):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base_url() + path, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else {}


def wait_ready(timeout: float) -> float:
    """等 /health 返回 200。首次启动要下载 ~70 GB 模型，可能很久。"""
    started = time.time()
    deadline = started + timeout
    last = ""
    last_note = 0.0
    while time.time() < deadline:
        try:
            request("/health", "", timeout=5)
            return time.time() - started
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - 连接被拒 / 超时都只是「还没起来」
            last = str(exc)
        waited = time.time() - started
        if waited - last_note >= 30:
            last_note = waited
            print(f"      等待中（{waited:.0f}s）：{last}")
            print("      首次启动会下载模型（约 70 GB），进度看 `pixi run strata-logs`")
        time.sleep(2)
    raise SystemExit(f"服务在 {timeout:.0f}s 内没有就绪（最后一次：{last}）")


def main() -> int:
    load_env()
    ok = True
    print(f"[1/4] 等待服务就绪：{base_url()}")
    waited = wait_ready(READY_TIMEOUT)
    print(f"      就绪，用时 {waited:.1f}s")

    key = api_key()
    if not key:
        ok = False
        print("      !! 没有可用的 API key：在 .env 里设置 STRATA_API_KEY，或 `pixi run strata-up` 后再试")
        print("         （首启会自动生成，用 `pixi run strata-key` 查看）")

    print("[2/4] 模型列表")
    model_id = "strata"
    try:
        models = (request("/v1/models", key) or {}).get("data") or []
        ids = [m.get("id") for m in models if isinstance(m, dict) and m.get("id")]
        print(f"      /v1/models -> {ids or '（空）'}")
        if ids:
            model_id = ids[0]
            extra = {k: v for k, v in models[0].items() if k in ("context_length", "max_model_len", "owned_by")}
            if extra:
                print(f"      {extra}")
        else:
            ok = False
            print("      !! 模型列表为空：模型可能还在加载")
    except urllib.error.HTTPError as exc:
        ok = False
        print(f"      !! /v1/models 失败：HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:200]}")
        if exc.code == 401:
            print("         key 不对：`pixi run strata-key` 打印当前的 key，填进 .env 的 STRATA_API_KEY")

    print("[3/4] chat 补全")
    started = time.time()
    try:
        resp = request("/v1/chat/completions", key, {
            "model": model_id,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": MAX_TOKENS,
            "temperature": 0,
            "stream": False,
        })
    except urllib.error.HTTPError as exc:
        print(f"      !! HTTP {exc.code}：{exc.read().decode('utf-8', 'replace')[:300]}")
        return 1
    wall = time.time() - started

    choices = resp.get("choices") or [{}]
    msg = (choices[0].get("message") or {})
    content = (msg.get("content") or "").strip()
    reasoning = (msg.get("reasoning_content") or "").strip()
    if reasoning:
        print(f"      思考：{reasoning[:120]!r}{' …' if len(reasoning) > 120 else ''}")
    print(f"      内容：{content[:200]!r}")
    if not content:
        ok = False
        print("      !! 返回内容为空（可能只输出到 max_tokens 里的思考段，调大 STRATA_SMOKE_MAX_TOKENS）")

    print("[4/4] 吞吐")
    usage = resp.get("usage") or {}
    completion = usage.get("completion_tokens") or 0
    prompt_tokens = usage.get("prompt_tokens") or 0
    print(f"      prompt {prompt_tokens} tok / 生成 {completion} tok / 墙钟 {wall:.2f}s")
    if completion and wall > 0:
        print(f"      端到端 {completion / wall:.1f} tok/s（含 prefill 和网络）")
    if not usage:
        print("      （服务没有返回 usage 字段，跳过吞吐统计）")

    ctx = os.environ.get("STRATA_CONTEXT", "")
    if ctx:
        print(f"\n      配置的上下文：{ctx}（.env STRATA_CONTEXT）；长上下文的实测用长 prompt 复核")

    print("\n===== 冒烟自检 " + ("通过" if ok else "失败") + " =====")
    if ok:
        print(f"接口：{base_url()}/v1  模型名：{model_id}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
