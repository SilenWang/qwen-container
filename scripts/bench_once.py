#!/usr/bin/env python3
"""单次请求基准：等就绪 → 发一个固定大小的请求 → 输出 JSON。

供 `scripts/tune_ncmoe.sh` 解析，也可手动用：

    pixi run up
    python scripts/bench_once.py --prompt-tokens 8192 --max-tokens 64

输出字段：ok / prompt_n / prefill_tps / decode_tps / ttft_s / wall_s / error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llama_client as lc  # noqa: E402

FILLER = ("A steady wind pushed the small boat toward the harbour while the crew counted "
          "the crates stacked along the pier. Every crate carried a stencil of a different "
          "number, and the clerk wrote each one down in a narrow ledger. ")


def build_prompt(tokens: int) -> str:
    if tokens <= 0:
        return "请用一句话说明 1+1 等于多少。"
    sample = FILLER * 20
    ratio = lc.count_tokens(sample) / len(sample)
    chars = int(tokens / ratio)
    body = (FILLER * (chars // len(FILLER) + 1))[:chars]
    body += "\n\n请用一句话总结上面这段文字。"
    return body


def main() -> int:
    lc.load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-tokens", type=int, default=512,
                        help="目标 prompt 长度（token），0 = 极短 prompt")
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--ready-timeout", type=float,
                        default=float(os.environ.get("READY_TIMEOUT", 1200)))
    args = parser.parse_args()

    result: dict = {"ok": False}
    try:
        t0 = time.time()
        lc.wait_ready(timeout=args.ready_timeout, verbose=False)
        result["ready_s"] = round(time.time() - t0, 1)
        resp = lc.chat(build_prompt(args.prompt_tokens), max_tokens=args.max_tokens,
                       cache_prompt=False)
        timings = resp.get("timings") or {}
        text = lc.completion_text(resp)
        result.update({
            "ok": bool(text.strip()) and (timings.get("predicted_n") or 0) > 0,
            "prompt_n": timings.get("prompt_n"),
            "prefill_tps": round(timings.get("prompt_per_second") or 0.0, 1),
            "decode_tps": round(timings.get("predicted_per_second") or 0.0, 1),
            "ttft_s": round((timings.get("prompt_ms") or 0.0) / 1000.0, 2),
            "wall_s": round(time.time() - t0, 1),
        })
    except Exception as exc:  # noqa: BLE001 — 基准脚本需要把失败也变成结构化输出
        result["error"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())