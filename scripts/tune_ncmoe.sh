#!/usr/bin/env bash
# 扫 --n-cpu-moe：逐档重建容器，记录「是否活着 + 显存占用 + 吞吐」，最后给出建议档位。
#
#   pixi run tune-ncmoe
#   NCMOE_SWEEP="32 30 28 26 24" pixi run tune-ncmoe
#
# 读法：N_CPU_MOE 越大 → 越多专家留在内存 → 显存越省、速度越慢。
# 所以按**从大到小**扫：第一档加载失败，就是显存墙的位置；建议值取「墙上面那一档再退一格」，
# 给计算缓冲留余量。真实长上下文请用 `pixi run longctx` 复核。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

if [ -f .env ]; then
  # shellcheck source=scripts/lib.sh
  . "$HERE/scripts/lib.sh"
  load_env "$HERE"
fi

# 默认从 36 开始（12G 卡的显存墙按张量实测值推算在 24–28 之间，36 起是安全的高端）
SWEEP="${NCMOE_SWEEP:-36 32 30 28 26 24 22}"
PROMPT_TOKENS="${BENCH_PROMPT_TOKENS:-2048}"
MAX_TOKENS="${BENCH_MAX_TOKENS:-64}"
CONTAINER="${CONTAINER_NAME:-qwen36-35b-a3b}"
# 第一档失败就停（再往下只会更吃显存），设 0 可跑完全部档位
BREAK_ON_FAIL="${BREAK_ON_FAIL:-1}"

command -v jq >/dev/null || { echo "缺少 jq（应由 pixi 提供，请用 pixi run tune-ncmoe 调用）" >&2; exit 1; }

vram_used() {
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' \
      | sed 's/$/ MiB/'
  else
    echo "n/a"
  fi
}

rows_file="$(mktemp)"
trap 'rm -f "$rows_file"' EXIT

echo "扫参档位：$SWEEP"
echo "每档：重建容器 → 等就绪 → 发 $PROMPT_TOKENS tok prompt / 生成 $MAX_TOKENS tok → 记录"
echo

last_ok=""
last_ok_prev=""
hit_wall=""

for n in $SWEEP; do
  printf '\033[1m=== N_CPU_MOE=%s ===\033[0m\n' "$n"

  if ! up_out="$(N_CPU_MOE="$n" docker compose up -d --force-recreate 2>&1)"; then
    echo "  ❌ docker compose up 失败："
    printf '%s\n' "$up_out" | sed 's/^/     /'
    printf '%s\t%s\t%s\t%s\t%s\n' "$n" "-" "-" "-" "启动失败" >> "$rows_file"
    hit_wall="$n"
    [ "$BREAK_ON_FAIL" = "1" ] && break
    continue
  fi

  bench="$(N_CPU_MOE="$n" python scripts/bench_once.py \
             --prompt-tokens "$PROMPT_TOKENS" --max-tokens "$MAX_TOKENS" 2>/dev/null || true)"
  [ -n "$bench" ] || bench='{"ok":false,"error":"无输出"}'

  state="$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo unknown)"
  vram="$(vram_used)"

  if [ "$(printf '%s' "$bench" | jq -r '.ok')" = "true" ]; then
    decode="$(printf '%s' "$bench" | jq -r '.decode_tps')"
    prefill="$(printf '%s' "$bench" | jq -r '.prefill_tps')"
    ttft="$(printf '%s' "$bench" | jq -r '.ttft_s')"
    echo "  prefill ${prefill} tok/s | decode ${decode} tok/s | ttft ${ttft}s | 显存 ${vram}"
    printf '%s\t%s\t%s\t%s\t%s\n' "$n" "$prefill" "$decode" "$vram" "ok" >> "$rows_file"
    last_ok_prev="$last_ok"
    last_ok="$n"
  else
    reason="失败"
    # 容器已退出基本都是显存不够（OOM）—— 说明这一档已经超过显存墙
    [ "$state" = "exited" ] && reason="OOM/退出"
    echo "  ❌ $reason：$(printf '%s' "$bench" | jq -r '.error // "未知"')"
    echo "  容器状态：$state；显存：$vram"
    printf '%s\t%s\t%s\t%s\t%s\n' "$n" "-" "-" "$vram" "$reason" >> "$rows_file"
    hit_wall="$n"
    [ "$BREAK_ON_FAIL" = "1" ] && break
  fi
done

echo
echo "================ 汇总 ================"
printf '%-10s %-16s %-16s %-16s %s\n' "N_CPU_MOE" "prefill(tok/s)" "decode(tok/s)" "显存" "结果"
while IFS=$'\t' read -r n prefill decode vram status; do
  printf '%-10s %-16s %-16s %-16s %s\n' "$n" "$prefill" "$decode" "$vram" "$status"
done < "$rows_file"

echo
if [ -n "$last_ok" ]; then
  if [ -n "$hit_wall" ]; then
    echo "显存墙在 N_CPU_MOE=$hit_wall（这一档起不来），最低可用档位是 $last_ok。"
  else
    echo "全部档位都跑通了，最低可用档位是 $last_ok（可以把 NCMOE_SWEEP 再往下调试试）。"
  fi
  echo "建议 N_CPU_MOE=$last_ok：写进 .env，然后"
  echo "  pixi run recreate && pixi run longctx      # 用真实长上下文复核，别只看短请求速度"
  if [ -n "$last_ok_prev" ]; then
    echo "（要稳一点的余量可以退一档到 $last_ok_prev，代价是更慢）"
  fi
else
  echo "😕 没有一档跑通。先 pixi run preflight 看环境，再看对应档位的 docker compose logs。"
  exit 1
fi