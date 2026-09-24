#!/usr/bin/env python3
"""冒烟自检：服务就绪 → 参数核对 → 一次 chat 补全 → 吞吐报告。

    pixi run smoke

退出码非 0 表示自检失败（供 CI / 脚本判断）。
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llama_client as lc  # noqa: E402

PROMPT = os.environ.get("SMOKE_PROMPT", "用一句话解释 MoE（混合专家）模型为什么省显存。")
MAX_TOKENS = int(os.environ.get("SMOKE_MAX_TOKENS", "96"))


def main() -> int:
    lc.load_env()
    expect_ctx = int(os.environ.get("CTX_SIZE", "131072"))
    ok = True

    print(f"[1/4] 等待服务就绪：{lc.base_url()}")
    waited = lc.wait_ready()
    print(f"      就绪，用时 {waited:.1f}s")

    print("[2/4] 核对服务参数")
    info = lc.server_info()
    print(f"      model_path    = {info['model_path']}")
    print(f"      n_ctx         = {info['n_ctx']}（期望 {expect_ctx}）")
    print(f"      total_slots   = {info['total_slots']}")
    print(f"      chat_template = {'有' if info['has_chat_template'] else '无'}")
    if info["n_ctx"] != expect_ctx:
        ok = False
        print(f"      !! n_ctx 与 CTX_SIZE 不一致：{info['n_ctx']} != {expect_ctx}")
        print("         常见原因：--parallel 调大后每个 slot 只分到 ctx/parallel")

    print("[3/4] chat 补全")
    started = time.time()
    resp = lc.chat(PROMPT, max_tokens=MAX_TOKENS)
    wall = time.time() - started
    content = lc.completion_text(resp).strip()
    print(f"      内容：{content[:200]!r}")
    if not content:
        ok = False
        print("      !! 返回内容为空")

    print("[4/4] 吞吐")
    t = resp.get("timings") or {}
    prompt_n = t.get("prompt_n") or 0
    pred_n = t.get("predicted_n") or 0
    print(f"      prefill: {prompt_n} tok / {t.get('prompt_ms', 0) / 1000:.2f}s"
          f" = {t.get('prompt_per_second', 0):.1f} tok/s")
    print(f"      decode : {pred_n} tok / {t.get('predicted_ms', 0) / 1000:.2f}s"
          f" = {t.get('predicted_per_second', 0):.1f} tok/s")
    print(f"      总耗时 : {wall:.2f}s")
    if pred_n < 1:
        ok = False
        print("      !! 没有生成任何 token")

    print("SMOKE OK" if ok else "SMOKE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())