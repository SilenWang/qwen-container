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

# 上下文只在没设置时给默认值；仍是模板默认值或别的档位常用值时按模型改写，避免大上下文在 12G 卡上直接 OOM
is_ctx_hint() {
  case " $ALL_CTX_HINTS " in *" $1 "*) return 0 ;; esac
  return 1
}
template_ctx="$(grep '^STRATA_CONTEXT=' .env.example | cut -d= -f2-)"
if ! grep -q '^STRATA_CONTEXT=.\+' .env; then
  set_env STRATA_CONTEXT "$CTX_HINT"
  echo "上下文未设置，按 $MODEL 的常用值写入 STRATA_CONTEXT=$CTX_HINT（显存紧张可调小）"
else
  cur_ctx="$(grep '^STRATA_CONTEXT=' .env | cut -d= -f2-)"
  if { [ "$cur_ctx" = "$template_ctx" ] || is_ctx_hint "$cur_ctx"; } && [ "$cur_ctx" != "$CTX_HINT" ]; then
    set_env STRATA_CONTEXT "$CTX_HINT"
    echo "上下文还是默认值或别的档位常用值 $cur_ctx，按 $MODEL 改写为 STRATA_CONTEXT=$CTX_HINT（显存够可以调回去）"
  elif [ "$cur_ctx" != "$CTX_HINT" ]; then
    echo "注意：当前 STRATA_CONTEXT=$cur_ctx，$MODEL 的常用值是 $CTX_HINT（显存不够会 OOM，够用就可以保留）"
  fi
fi

# Inkling 从 32K 起把 KV 卸载到 host（内存）：按机器内存核算，装不下就降到装得下的档位。
# 128K / 131072 在 16 GiB 内存的机器上重载时会被 OOM killer 结束，3 次后 Strata 熔断（客户端看到 503）。
if [ "$FAMILY" = "inkling-2-mini" ] && command -v python3 >/dev/null 2>&1; then
  mem_gib="${STRATA_MEM_GIB:-$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo 2>/dev/null || echo 0)}"
  cur_ctx="$(grep '^STRATA_CONTEXT=' .env | cut -d= -f2- || true)"
  budget="$(python3 "$HERE/scripts/ctx_budget.py" --family "$FAMILY" --model "$MODEL" \
              --context "${cur_ctx:-$CTX_HINT}" --memory "$mem_gib" 2>/dev/null || true)"
  b_status="$(printf '%s\n' "$budget" | sed -n 's/^status=//p' | head -1)"
  b_suggested="$(printf '%s\n' "$budget" | sed -n 's/^suggested=//p' | head -1)"
  b_msg="$(printf '%s\n' "$budget" | sed -n 's/^message=//p' | head -1)"
  case "$b_status" in
    bad | warn)
      if [ -n "$b_suggested" ] && [ "$b_suggested" != "$cur_ctx" ]; then
        set_env STRATA_CONTEXT "$b_suggested"
        echo "内存核算：上下文 ${cur_ctx:-$CTX_HINT} 在 ${mem_gib} GiB 内存上装不下，已改写为 STRATA_CONTEXT=$b_suggested"
        [ -n "$b_msg" ] && echo "  $b_msg"
      fi
      ;;
  esac
fi

echo "切到 $LABEL"
echo "  STRATA_FAMILY=$FAMILY  STRATA_MODEL=$MODEL  STRATA_PRESET=$PRESET"
echo "  $NOTE"

if ! docker info >/dev/null 2>&1; then
  echo
  echo "docker daemon 不可达，只改好了 .env；能起容器时执行：pixi run recreate" >&2
  exit 0
fi

echo "重建容器（会先卸载旧模型；首次加载新模型需要下载权重）..."
docker compose up -d --force-recreate
echo
echo "看进度：pixi run logs      自检：pixi run smoke"
