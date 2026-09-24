#!/usr/bin/env python3
"""长上下文阶梯实测：在指定深度埋一个随机 passcode，看模型能否召回，并记录 prefill / decode 吞吐。

    pixi run longctx
    pixi run longctx -- --depths 8192,32768,65536,122000
    DEPTHS=4096,16384 pixi run longctx

为什么必须实测：MoE 专家卸载到内存后，prefill 是算力瓶颈（所有 token 都要过全部专家），
而 decode 是带宽瓶颈。两者的 tok/s 差异很大，且随上下文深度衰减。128K 能不能用，
只有跑一遍才知道。
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llama_client as lc  # noqa: E402

FILLER = (
    "The lighthouse keeper kept a careful log of every ship that passed the reef at dawn. "
    "Weather reports arrived by radio, and the ink on the pages slowly faded in the salt air. "
    "Nothing unusual happened for weeks, which was exactly how the keeper preferred it. "
)
QUESTION = "上面这份文档里记录的 vault passcode 是什么？只回答 passcode 本身，不要解释。"
MAX_TOKENS = int(os.environ.get("LONGCTX_MAX_TOKENS", "32"))
NEEDLE_DEPTH = float(os.environ.get("NEEDLE_DEPTH", "0.5"))


def make_code() -> str:
    return "ZX-{}-{}".format(random.randint(1000, 9999), random.randint(1000, 9999))


def insert_needle(body: str, needle: str, frac: float) -> str:
    pos = int(len(body) * frac)
    # 对齐到空白处，避免把句子切断
    space = body.find(" ", pos)
    if space != -1:
        pos = space + 1
    return body[:pos] + needle + " " + body[pos:]


def assemble(body: str, needle: str) -> str:
    """最终发给模型的 prompt：filler（含 needle）+ 提问。"""
    return f"{insert_needle(body, needle, NEEDLE_DEPTH)}\n\n{QUESTION}"


def build_prompt(budget_tokens: int, needle: str) -> tuple[str, int]:
    """构造一个整 prompt token 数贴近 budget 的请求体。

    用 /tokenize 校准（而不是猜 chars/token）——这样表格里的「目标深度」和「实际 prompt」
    才对得上；顺便也把 needle 与提问模板的开销算进去了。
    """
    sample = FILLER * 20
    ratio = lc.count_tokens(sample) / len(sample)  # tokens/char
    chars = int(budget_tokens / ratio)
    prompt, n = assemble((FILLER * (chars // len(FILLER) + 1))[:chars], needle), 0
    for _ in range(12):
        prompt = assemble((FILLER * (chars // len(FILLER) + 1))[:chars], needle)
        n = lc.count_tokens(prompt)
        if abs(n - budget_tokens) <= max(16, int(budget_tokens * 0.005)):
            break
        chars = max(64, int(chars * budget_tokens / max(n, 1)))
    return prompt, n


def template_overhead() -> int:
    """量一次 chat 模板本身占多少 token（模板包裹 /tokenize 看不到），用于校准预算。"""
    probe = "ping"
    resp = lc.chat(probe, max_tokens=1, cache_prompt=False)
    prompt_n = (resp.get("timings") or {}).get("prompt_n") or 0
    return max(0, prompt_n - lc.count_tokens(probe))


def run_depth(depth: int, ctx: int, overhead: int) -> dict:
    needle_code = make_code()
    needle = f"IMPORTANT: the vault passcode for this document is {needle_code}."
    # 预留输出长度与一点余量，再扣掉模板开销，避免实际 prompt 超出 n_ctx
    budget = min(depth, ctx - MAX_TOKENS - 512) - overhead
    prompt, prompt_tokens = build_prompt(budget, needle)

    print(f"  → 深度 {depth}：构造 prompt {prompt_tokens} tok（needle 在 {NEEDLE_DEPTH:.0%} 处），"
          f"发起请求 ...", flush=True)
    started = time.time()
    resp = lc.chat(prompt, max_tokens=MAX_TOKENS, cache_prompt=False)
    wall = time.time() - started
    answer = lc.completion_text(resp)
    timings = resp.get("timings") or {}

    return {
        "target": depth,
        "prompt_n": timings.get("prompt_n", prompt_tokens),
        "prefill_tps": timings.get("prompt_per_second", 0.0),
        "prefill_s": timings.get("prompt_ms", 0.0) / 1000.0,
        "decode_tps": timings.get("predicted_per_second", 0.0),
        "wall_s": wall,
        "code": needle_code,
        "hit": needle_code in answer,
        "answer": answer.strip()[:80],
    }


def main() -> int:
    lc.load_env()
    parser = argparse.ArgumentParser(description="长上下文阶梯实测")
    parser.add_argument("--depths", default=os.environ.get("DEPTHS", "8192,32768,65536,122000"),
                        help="逗号分隔的目标 token 深度")
    args = parser.parse_args()

    ctx = int(os.environ.get("CTX_SIZE", "131072"))
    depths = [int(d) for d in args.depths.split(",") if d.strip()]

    print(f"等待服务就绪：{lc.base_url()}")
    lc.wait_ready()
    info = lc.server_info()
    overhead = template_overhead()
    print(f"服务 n_ctx = {info['n_ctx']}（脚本按 CTX_SIZE={ctx} 计算预算，"
          f"chat 模板开销 {overhead} tok）\n")

    rows = []
    for depth in depths:
        rows.append(run_depth(depth, ctx, overhead))

    print(f"\n{'目标深度':>10} {'实际prompt':>11} {'prefill':>13} {'prefill耗时':>12} "
          f"{'decode':>12} {'召回':>6}")
    print("-" * 72)
    for r in rows:
        print(f"{r['target']:>10} {r['prompt_n']:>11} {r['prefill_tps']:>10.1f} tok/s "
              f"{r['prefill_s']:>10.1f}s {r['decode_tps']:>9.1f} tok/s "
              f"{'✅' if r['hit'] else '❌':>6}")
    print()
    for r in rows:
        print(f"  {r['target']} → passcode {r['code']}，回答：{r['answer']!r}")

    failed = [r for r in rows if not r["hit"]]
    if failed:
        print(f"\n⚠️  {len(failed)}/{len(rows)} 档未召回，长上下文可靠性存疑")
        return 1
    print("\n全部档位召回成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())