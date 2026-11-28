#!/usr/bin/env python3
"""上下文预算：把 STRATA_CONTEXT 与机器内存换算成 Inkling 的 host KV 需求。

Inkling 从 0.2.20 起在 32K 以上会把 KV 卸载到 host（内存，见 docs/models.md）。32K 时
host KV 实测 1.07 GiB（镜像 0.2.21 的 `inkling_fit.compute('SQ-10', 12 GiB, ctx=32768)`），
之后按 token 数线性增长。

`INKLING_MIN_RAM_GIB` 是上游模型卡给的**官方最低内存门槛**（SQ-10 16 GiB），只用于在低配
机器上提前拦截，**不是任何一台现场机器的容量**。判定必须用脚本实际读到的 MemTotal（或
STRATA_MEM_GIB 预演值）：所以 128K 在 128 GiB 的机器上判 ok，只有「低于官方门槛 + host KV」
的机器才判 bad。

低于门槛时引擎可能在加载 / 重新加载时因内存不足被结束，Strata 0.2.21 起同一故障 10 分钟内
3 次就熔断，客户端看到 503 "The engine stopped 3 times in 10 minutes"。但这条 503 也可能来自
其它退出原因（显存、SIGILL、SIGBUS 等），先跑 `pixi run diagnose` 收证据，别把低配判断当成
现场结论。

preflight 用它做启动前准入（`pixi run preflight`），select_model 用它决定切到 Inkling 时
要不要把上下文降到装得下的档位。两个脚本都只读这里的输出：

    python3 scripts/ctx_budget.py --family inkling-2-mini --model SQ-10 \
        --context 128k --memory 16

    status=bad
    context=128k
    context_tokens=131072
    host_kv_gib=4.28
    required_gib=19.2
    suggested=32k
    message=...

status：ok / warn（贴着内存上限）/ bad（装不下）/ unknown（拿不到内存数字）/ skip（不是 Inkling）。
"""

from __future__ import annotations

import argparse
import json

INKLING_FAMILIES = {"inkling-2-mini", "inkling", "inkling2"}
CTX_BASE_TOKENS = 32768
#: 0.2.21 镜像里 `inkling_fit.compute('SQ-10', 12 GiB, ctx=32768)` 的 host_kv_gb
INKLING_HOST_KV_GIB_AT_32K = 1.07
#: 各档位的**官方最低内存门槛**（模型卡给的整机内存，已含系统余量），见 docs/models.md。
#: 只用于低配拦截，不是现场机器的容量。
INKLING_MIN_RAM_GIB = {"SQ-10": 16.0, "SQ-15": 24.0, "SQ-20": 32.0}
DEFAULT_MIN_RAM_GIB = 16.0
#: Strata 的上下文档位；给出建议值时只在这些值里挑
CTX_CHOICES = (8192, 32768, 65536, 131072, 262144)


def parse_context(raw: str | int | None) -> int | None:
    """`32k` / `32K` / `131072` / `32768` → token 数；空值或无法解析 → None。"""
    if raw is None:
        return None
    text = str(raw).strip().lower().replace("_", "")
    if not text:
        return None
    mult = 1
    if text.endswith("k"):
        text, mult = text[:-1], 1024
    elif text.endswith("m"):
        text, mult = text[:-1], 1024 * 1024
    try:
        value = int(float(text) * mult)
    except ValueError:
        return None
    return value if value > 0 else None


def format_context(tokens: int) -> str:
    """131072 → `128k`；不是整 K 的原样写 token 数。"""
    if tokens % 1024 == 0:
        return f"{tokens // 1024}k"
    return str(tokens)


def host_kv_gib(tokens: int) -> float:
    """Inkling 在给定上下文下放在 host 的 KV（GiB），按 32K 实测值线性外推。"""
    return INKLING_HOST_KV_GIB_AT_32K * (tokens / CTX_BASE_TOKENS)


def inkling_budget(model: str | None, tokens: int, memory_gib: float | None) -> dict:
    """算一个上下文是否装得下：官方内存门槛 + 超过 32K 的那部分 host KV。"""
    name = (model or "").strip().upper()
    base = INKLING_MIN_RAM_GIB.get(name, DEFAULT_MIN_RAM_GIB)
    kv = host_kv_gib(tokens)
    required = base + max(0.0, kv - INKLING_HOST_KV_GIB_AT_32K)
    ok_ctx = format_context(tokens)
    out = {
        "context": ok_ctx,
        "context_tokens": tokens,
        "host_kv_gib": round(kv, 2),
        "required_gib": round(required, 2),
        "suggested": None,
        "status": "ok",
        "message": "",
    }
    if not memory_gib or memory_gib <= 0:
        out.update(status="unknown", message="拿不到机器内存，无法核算上下文；32K 以上请自行确认内存")
        return out

    fits = [c for c in CTX_CHOICES if base + max(0.0, host_kv_gib(c) - INKLING_HOST_KV_GIB_AT_32K) <= memory_gib]
    best = max(fits) if fits else min(CTX_CHOICES)
    if required <= memory_gib:
        out["status"] = "ok"
        out["message"] = (
            f"上下文 {ok_ctx}：host KV 约 {kv:.2f} GiB，按 {name or 'Inkling'} 的官方最低 {base:g} GiB 门槛算需 "
            f"{required:.1f} GiB，本机实际 {memory_gib:g} GiB，够用"
        )
    elif required <= memory_gib + 1.0:
        out.update(
            status="warn",
            suggested=format_context(best),
            message=(
                f"上下文 {ok_ctx}：host KV 约 {kv:.2f} GiB，按官方最低 {base:g} GiB 门槛算需 {required:.1f} GiB，"
                f"本机实际 {memory_gib:g} GiB —— 贴边；建议 STRATA_CONTEXT={format_context(best)}"
            ),
        )
    else:
        out.update(
            status="bad",
            suggested=format_context(best),
            message=(
                f"上下文 {ok_ctx} 低于官方门槛：host KV 约 {kv:.2f} GiB，按 {name or 'Inkling'} 的官方最低 "
                f"{base:g} GiB 门槛算需 {required:.1f} GiB，本机实际只有 {memory_gib:g} GiB。重载时内存可能不够"
                f"（引擎被结束，3 次后 503）；改成 STRATA_CONTEXT={format_context(best)}（pixi run recreate）"
            ),
        )
    return out


def assess(family: str | None, model: str | None, context: str | None, memory_gib: float | None) -> dict:
    """入口：非 Inkling 家族直接 skip（KV 由引擎 / 显存在管），否则算预算。"""
    if (family or "").strip().lower() not in INKLING_FAMILIES:
        return {"status": "skip", "message": f"family={family or '(空)'} 不是 Inkling，跳过 host KV 核算"}
    tokens = parse_context(context)
    if tokens is None:
        return {"status": "unknown", "message": f"无法解析 STRATA_CONTEXT={context!r}（写 32k / 64k / 128k 或 token 数）"}
    return {"model": (model or "").strip() or None, **inkling_budget(model, tokens, memory_gib)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inkling 上下文与内存预算")
    parser.add_argument("--family", default="inkling-2-mini")
    parser.add_argument("--model", default="SQ-10")
    parser.add_argument("--context", default=None, help="32k / 64k / 128k / 256k，或 token 数")
    parser.add_argument("--memory", type=float, default=None, help="机器内存（GiB）；不给则只做解析")
    parser.add_argument("--format", choices=("kv", "json"), default="kv", help="kv（默认，shell 友好）或 json")
    args = parser.parse_args(argv)

    result = assess(args.family, args.model, args.context, args.memory)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for key in ("status", "context", "context_tokens", "host_kv_gib", "required_gib", "suggested", "message"):
            if key in result and result[key] is not None:
                print(f"{key}={result[key]}")
    return 1 if result.get("status") == "bad" else 0


if __name__ == "__main__":
    raise SystemExit(main())
