#!/usr/bin/env python3
"""长上下文召回实测：在指定深度埋一个随机 passcode，看模型能否召回，并记录端到端速度。

    pixi run longctx
    pixi run longctx -- --depths 4096,16384,32768
    DEPTHS=8192,16384 pixi run longctx

MoE 专家按需在显存 / 内存之间调度时，长上下文的 prefill 是瓶颈，召回也可能变差；
128K / 32K 能不能用只有跑一遍才知道。Strata 的 OpenAI 兼容接口不返回 llama.cpp 的
timings，所以这里报的是端到端速度（含 prefill、decode 和网络），不是纯 decode 吞吐。
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import client as c  # noqa: E402

FILLER = (
    "The lighthouse keeper kept a careful log of every ship that passed the reef at dawn. "
    "Weather reports arrived by radio, and the ink on the pages slowly faded in the salt air. "
    "Nothing unusual happened for weeks, which was exactly how the keeper preferred it. "
)
QUESTION = "上面这份文档里记录的 vault passcode 是什么？只回答 passcode 本身，不要解释。"
# 关掉思考后 64 tok 足够回答一个 passcode；万一模板不认 enable_thinking，按 RETRY_TOKENS 重试一次
MAX_TOKENS = int(os.environ.get("LONGCTX_MAX_TOKENS", "64"))
RETRY_TOKENS = int(os.environ.get("LONGCTX_RETRY_TOKENS", "512"))
NEEDLE_DEPTH = float(os.environ.get("NEEDLE_DEPTH", "0.5"))
# filler 的字符/token 比例，实测约 4.6；每个深度跑完会用 usage.prompt_tokens 自动校正
CHARS_PER_TOKEN = float(os.environ.get("CHARS_PER_TOKEN", "4.6"))
chars_per_token = CHARS_PER_TOKEN


def context_tokens() -> int:
    raw = os.environ.get("STRATA_CONTEXT", "128k").strip().lower()
    if raw.endswith("k") and raw[:-1].isdigit():
        return int(raw[:-1]) * 1024
    return int(raw) if raw.isdigit() else 131072


def default_depths() -> str:
    ctx = context_tokens()
    depths = [d for d in (8192, 16384, 32768, 65536) if d <= ctx * 0.8]
    return ",".join(str(d) for d in (depths or [ctx // 2]))


def make_code() -> str:
    return "ZX-{}-{}".format(random.randint(1000, 9999), random.randint(1000, 9999))


def build_prompt(budget_tokens: int, needle: str, cpt: float) -> str:
    chars = max(64, int(budget_tokens * cpt))
    body = (FILLER * (chars // len(FILLER) + 1))[:chars]
    pos = body.find(" ", int(len(body) * NEEDLE_DEPTH))
    pos = pos + 1 if pos != -1 else len(body) // 2
    return f"{body[:pos]}{needle} {body[pos:]}\n\n{QUESTION}"


def run_depth(depth: int, ctx: int, key: str, model: str) -> dict:
    global chars_per_token
    needle_code = make_code()
    needle = f"IMPORTANT: the vault passcode for this document is {needle_code}."
    budget = min(depth, ctx - MAX_TOKENS - 512)
    prompt = build_prompt(budget, needle, chars_per_token)

    print(f"  → 深度 {depth}：约 {budget} tok（needle 在 {NEEDLE_DEPTH:.0%} 处），发起请求 ...", flush=True)
    started = time.time()
    resp = c.chat(prompt, MAX_TOKENS, key=key, model=model)
    answer = c.completion_text(resp)
    if not answer:
        # 思考段吃光预算时正文为空，会误判成「未召回」：放大预算重试一次
        print(f"      正文为空，按 {RETRY_TOKENS} tok 重试一次（思考段可能吃掉了预算）")
        resp = c.chat(prompt, RETRY_TOKENS, key=key, model=model)
        answer = c.completion_text(resp)
    wall = time.time() - started
    usage = resp.get("usage") or {}

    # 用实际 prompt token 数回读比例，后续深度更贴近目标
    actual = usage.get("prompt_tokens") or 0
    if actual and budget:
        chars_per_token = max(2.0, min(8.0, chars_per_token * budget / actual))

    return {
        "target": depth,
        "prompt_n": actual,
        "completion_n": usage.get("completion_tokens") or 0,
        "wall_s": wall,
        "code": needle_code,
        "hit": needle_code in answer,
        "answer": answer.strip()[:80],
    }


def main() -> int:
    c.load_env()
    parser = argparse.ArgumentParser(description="长上下文召回实测")
    parser.add_argument("--depths", default=os.environ.get("DEPTHS") or default_depths(),
                        help="逗号分隔的目标 token 深度")
    args = parser.parse_args()

    ctx = context_tokens()
    depths = [int(d) for d in args.depths.split(",") if d.strip()]

    print(f"等待服务就绪：{c.base_url()}")
    c.wait_ready()
    key = c.api_key()
    if not key:
        raise SystemExit("没有可用的 API key：在 .env 里设置 STRATA_API_KEY，或 `pixi run up` 后再试")
    ids = c.model_ids(key)
    model = ids[0] if ids else "strata"
    print(f"模型 {model}；配置上下文 {ctx} tok（.env STRATA_CONTEXT），测试深度 {depths}\n")

    rows = []
    for depth in depths:
        rows.append(run_depth(depth, ctx, key, model))

    print(f"\n{'目标深度':>10} {'实际prompt':>11} {'生成':>6} {'墙钟':>8} {'端到端':>14} {'召回':>6}")
    print("-" * 66)
    for r in rows:
        speed = f"{r['completion_n'] / r['wall_s']:.1f} tok/s" if r["wall_s"] else "n/a"
        print(f"{r['target']:>10} {r['prompt_n']:>11} {r['completion_n']:>6} "
              f"{r['wall_s']:>7.1f}s {speed:>14} {'✅' if r['hit'] else '❌':>6}")
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
