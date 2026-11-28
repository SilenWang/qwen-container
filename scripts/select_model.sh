#!/usr/bin/env bash
# 切换模型：改写 .env 里的 FAMILY / MODEL / PRESET，再重建容器。
#
#   pixi run model-flash     # Qwen3.8-Flash-Next（默认）
#   pixi run model-35b       # Qwen3.6-35B-A3B
#   pixi run model-inkling   # Inkling 2 Mini（12G 卡 100% 显存）
#
# 所有模型共用一个 Strata 服务和一个数据目录；切换只是换容器启动参数，
# 已下载的权重和各自的 preset 都留着，切回来不会重新下载。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

# 各档位的常用上下文；切换时只在当前值还是模板默认 / 别的档位常用值时改写
ALL_CTX_HINTS="128k 32k"

case "${1:-}" in
  flash)
    FAMILY=qwen
    MODEL=IQ2_XS
    PRESET=flash-next
    CTX_HINT=128k
    LABEL="Qwen3.8-Flash-Next（qwen / IQ2_XS）"
    NOTE="下载约 68 GB，内存建议 64 GiB"
    ;;
  35b)
    FAMILY=qwen36
    MODEL=UD-Q4_K_XL
    PRESET=qwen36-35b
    CTX_HINT=32k
    LABEL="Qwen3.6-35B-A3B（qwen36 / UD-Q4_K_XL）"
    NOTE="下载约 23 GB，内存 32 GiB 起"
    ;;
  inkling)
    FAMILY=inkling-2-mini
    MODEL=SQ-10
    PRESET=inkling-2-mini
    CTX_HINT=32k
    LABEL="Inkling 2 Mini（inkling-2-mini / SQ-10）"
    NOTE="下载约 10.5 GB，12G 卡 32K 可用（冷专家自动放内存/SSD），内存 16 GiB 起；魔搭 / HuggingFace 均可"
    ;;
  *)
    echo "用法：$0 flash|35b|inkling" >&2
    exit 2
    ;;
esac

if [ ! -f .env ]; then
  echo "没有 .env，先从 .env.example 复制一份"
  cp .env.example .env
fi

set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

set_env STRATA_FAMILY "$FAMILY"
set_env STRATA_MODEL "$MODEL"
set_env STRATA_PRESET "$PRESET"

# 上下文：
#   - 没设置 → 写入该档位的常用值；
#   - Inkling → 交给下面的内存预算：装得下就保留用户的值（128k / 131072 都不改），只有 warn / bad 才降档；
#   - 其它档位 → 当前值还是模板默认 / 别的档位常用值时，按该档位常用值规范化。
is_ctx_hint() {
  case " $ALL_CTX_HINTS " in *" $1 "*) return 0 ;; esac
  return 1
}
template_ctx="$(grep '^STRATA_CONTEXT=' .env.example | cut -d= -f2-)"
cur_ctx="$(grep '^STRATA_CONTEXT=' .env | cut -d= -f2- || true)"
if [ -z "$cur_ctx" ]; then
  set_env STRATA_CONTEXT "$CTX_HINT"
  echo "上下文未设置，按 $MODEL 的常用值写入 STRATA_CONTEXT=$CTX_HINT（显存紧张可调小）"
elif [ "$FAMILY" = "inkling-2-mini" ]; then
  echo "当前 STRATA_CONTEXT=$cur_ctx：按机器内存核算，装不下才降档（见下）"
elif { [ "$cur_ctx" = "$template_ctx" ] || is_ctx_hint "$cur_ctx"; } && [ "$cur_ctx" != "$CTX_HINT" ]; then
  set_env STRATA_CONTEXT "$CTX_HINT"
  echo "上下文还是默认值或别的档位常用值 $cur_ctx，按 $MODEL 改写为 STRATA_CONTEXT=$CTX_HINT（显存够可以调回去）"
elif [ "$cur_ctx" != "$CTX_HINT" ]; then
  echo "注意：当前 STRATA_CONTEXT=$cur_ctx，$MODEL 的常用值是 $CTX_HINT（够用就可以保留）"
fi

# Inkling 从 32K 起把 KV 卸载到 host（内存）：按机器实际内存核算，只有装不下（warn / bad）才降到建议档位。
# 官方最低门槛（SQ-10 16 GiB）只用于低配拦截，不是现场机器的容量：128 GiB 这类机器保留用户写的 128k / 131072。
if [ "$FAMILY" = "inkling-2-mini" ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "没有 python3：跳过 Inkling 的上下文内存核算，保留 STRATA_CONTEXT 原值" >&2
  else
    mem_gib="${STRATA_MEM_GIB:-$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo 2>/dev/null || echo 0)}"
    cur_ctx="$(grep '^STRATA_CONTEXT=' .env | cut -d= -f2- || true)"
    budget="$(python3 "$HERE/scripts/ctx_budget.py" --family "$FAMILY" --model "$MODEL" \
                --context "${cur_ctx:-$CTX_HINT}" --memory "$mem_gib" 2>/dev/null || true)"
    b_status="$(printf '%s\n' "$budget" | sed -n 's/^status=//p' | head -1)"
    b_suggested="$(printf '%s\n' "$budget" | sed -n 's/^suggested=//p' | head -1)"
    b_msg="$(printf '%s\n' "$budget" | sed -n 's/^message=//p' | head -1)"
    case "$b_status" in
      ok)
        # 预算够：不改用户的值（128k 与 131072 都保留），只说明核算结果
        [ -n "$b_msg" ] && echo "内存核算：$b_msg → 保留 STRATA_CONTEXT=${cur_ctx:-$CTX_HINT}"
        ;;
      bad | warn)
        if [ -n "$b_suggested" ] && [ "$b_suggested" != "$cur_ctx" ]; then
          set_env STRATA_CONTEXT "$b_suggested"
          echo "内存核算：上下文 ${cur_ctx:-$CTX_HINT} 在 ${mem_gib} GiB 内存上装不下，已改写为 STRATA_CONTEXT=$b_suggested"
          [ -n "$b_msg" ] && echo "  $b_msg"
        fi
        ;;
      *)
        [ -n "$b_msg" ] && echo "内存核算：$b_msg（保留 STRATA_CONTEXT=${cur_ctx:-$CTX_HINT}）"
        ;;
    esac
  fi
fi

echo "切到 $LABEL"
echo "  STRATA_FAMILY=$FAMILY  STRATA_MODEL=$MODEL  STRATA_PRESET=$PRESET"
echo "  $NOTE"

if [ "${STRATA_SELECT_NO_UP:-0}" = "1" ]; then
  echo
  echo "只改写 .env，没有重建容器（STRATA_SELECT_NO_UP=1）"
  exit 0
fi

if ! docker info >/dev/null 2>&1; then
  echo
  echo "docker daemon 不可达，只改好了 .env；能起容器时执行：pixi run recreate" >&2
  exit 0
fi

echo "重建容器（会先卸载旧模型；首次加载新模型需要下载权重）..."
docker compose up -d --force-recreate
echo
echo "看进度：pixi run logs      自检：pixi run smoke"
