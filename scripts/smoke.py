#!/usr/bin/env python3
"""冒烟自检：就绪 → 模型列表 → 一次 chat 补全 → 吞吐。

    pixi run smoke

退出码非 0 表示自检失败（供 CI / 脚本判断）。
"""

from __future__ import annotations

import os
import sys
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import client as c  # noqa: E402

PROMPT = os.environ.get("SMOKE_PROMPT", "用一句话解释 MoE（混合专家）模型为什么省显存。")
MAX_TOKENS = int(os.environ.get("SMOKE_MAX_TOKENS", "96"))


def main() -> int:
    c.load_env()
    ok = True
    family = os.environ.get("STRATA_FAMILY", "qwen")
    model = os.environ.get("STRATA_MODEL", "IQ2_XS")
    preset = os.environ.get("STRATA_PRESET", "flash-next")
    print(f"配置：family={family} model={model} preset={preset} context={os.environ.get('STRATA_CONTEXT', '自动')}")

    print(f"[1/4] 等待服务就绪：{c.base_url()}")
    waited = c.wait_ready()
    print(f"      就绪，用时 {waited:.1f}s")

    key = c.api_key()
    if not key:
        ok = False
        print("      !! 没有可用的 API key：在 .env 里设置 STRATA_API_KEY，或 `pixi run up` 后再试")
        print("         （首启会自动生成，用 `pixi run key` 查看）")

    print("[2/4] 模型列表")
    model_id = "strata"
    try:
        ids = c.model_ids(key)
        print(f"      /v1/models -> {ids or '（空）'}")
        if ids:
            model_id = ids[0]
        else:
            ok = False
            print("      !! 模型列表为空：模型可能还在加载")
    except urllib.error.HTTPError as exc:
        ok = False
        print(f"      !! /v1/models 失败：HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:200]}")
        if exc.code == 401:
            print("         key 不对：`pixi run key` 打印当前的 key，填进 .env 的 STRATA_API_KEY")

    print("[3/4] chat 补全")
    started = time.time()
    try:
        resp = c.chat(PROMPT, MAX_TOKENS, key=key, model=model_id)
    except urllib.error.HTTPError as exc:
        print(f"      !! HTTP {exc.code}：{exc.read().decode('utf-8', 'replace')[:300]}")
        return 1
    wall = time.time() - started

    content = c.completion_text(resp)
    reasoning = ((resp.get("choices") or [{}])[0].get("message") or {}).get("reasoning_content") or ""
    if reasoning:
        print(f"      思考：{reasoning[:120]!r}{' …' if len(reasoning) > 120 else ''}")
    print(f"      内容：{content[:200]!r}")
    if not content:
        ok = False
        print("      !! 返回内容为空（可能只输出到 max_tokens 里的思考段，调大 SMOKE_MAX_TOKENS）")

    print("[4/4] 吞吐")
    usage = resp.get("usage") or {}
    completion_tokens = usage.get("completion_tokens") or 0
    prompt_tokens = usage.get("prompt_tokens") or 0
    print(f"      prompt {prompt_tokens} tok / 生成 {completion_tokens} tok / 墙钟 {wall:.2f}s")
    if completion_tokens and wall > 0:
        print(f"      端到端 {completion_tokens / wall:.1f} tok/s（含 prefill 和网络）")

    print("\n===== 冒烟自检 " + ("通过" if ok else "失败") + " =====")
    if ok:
        print(f"接口：{c.base_url()}/v1  模型名：{model_id}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
