#!/usr/bin/env bash
# 切换模型：改写 .env 里的 FAMILY / MODEL / PRESET，再重建容器。
#
#   pixi run model-35b     # Qwen3.6-35B-A3B
#   pixi run model-flash   # Qwen3.8-Flash-Next
#
# 两个模型共用一个 Strata 服务和一个数据目录；切换只是换容器启动参数，
# 已下载的权重和各自的 preset 都留着，切回来不会重新下载。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

case "${1:-}" in
  flash)
    FAMILY=qwen
    MODEL=IQ2_XS
    PRESET=flash-next
    CTX_HINT=128k
    LABEL="Qwen3.8-Flash-Next（qwen / IQ2_XS）"
    ;;
  35b)
    FAMILY=qwen36
    MODEL=UD-Q4_K_XL
    PRESET=qwen36-35b
    CTX_HINT=32k
    LABEL="Qwen3.6-35B-A3B（qwen36 / UD-Q4_K_XL）"
    ;;
  *)
    echo "用法：$0 flash|35b" >&2
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

# 上下文只在没设置时给默认值，不动用户自己调过的值
if ! grep -q '^STRATA_CONTEXT=.\+' .env; then
  set_env STRATA_CONTEXT "$CTX_HINT"
  echo "上下文未设置，按 $MODEL 的常用值写入 STRATA_CONTEXT=$CTX_HINT（显存紧张可调小）"
else
  cur_ctx="$(grep '^STRATA_CONTEXT=' .env | cut -d= -f2-)"
  if [ "$cur_ctx" != "$CTX_HINT" ]; then
    echo "注意：当前 STRATA_CONTEXT=$cur_ctx，$MODEL 的常用值是 $CTX_HINT（显存不够会 OOM，够用就可以保留）"
  fi
fi

echo "切到 $LABEL"
echo "  STRATA_FAMILY=$FAMILY  STRATA_MODEL=$MODEL  STRATA_PRESET=$PRESET"

if ! docker info >/dev/null 2>&1; then
  echo
  echo "docker daemon 不可达，只改好了 .env；能起容器时执行：pixi run recreate" >&2
  exit 0
fi

echo "重建容器（会先卸载旧模型；首次加载新模型需要下载权重）..."
docker compose up -d --force-recreate
echo
echo "看进度：pixi run logs      自检：pixi run smoke"
