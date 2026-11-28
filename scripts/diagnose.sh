#!/usr/bin/env bash
# 现场诊断：把「引擎连续退出 / 503 The engine stopped 3 times in 10 minutes」的判断依据一次收齐。
#
#   pixi run diagnose                 # 写到 output/diagnose-<时间>.txt
#   pixi run diagnose -- --print      # 同时打印到终端
#
# 收集：宿主内存 / /dev/shm / 数据目录磁盘、dmesg 的 OOM 记录、compose 与实际容器的内存限制、
# 服务日志（含 `the engine stopped ... (exit code N)`）、引擎日志末尾、容器内 ulimit / 磁盘 / GPU。
# 不改任何配置，也不重启容器。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
# shellcheck source=lib.sh
. "$HERE/scripts/lib.sh"
load_env "$HERE"

PRINT=0
for arg in "$@"; do
  case "$arg" in
    --print) PRINT=1 ;;
    -h | --help) sed -n '2,10p' "$0"; exit 0 ;;
  esac
done

OUT_DIR="$HERE/output"
mkdir -p "$OUT_DIR" 2>/dev/null || OUT_DIR="$PWD"
FILE="$OUT_DIR/diagnose-$(date +%Y%m%d-%H%M%S).txt"
DATA_DIR="${STRATA_DATA_DIR:-$HERE/strata-data}"

sec() { printf '\n== %s ==\n' "$*"; }
host() { "$@" 2>&1 | sed 's/^/  /'; }
compose() { docker compose "$@" 2>&1 | sed 's/^/  /'; }
cont() {
  printf '  $ %s\n' "$1"
  docker compose exec -T strata sh -c "$1" 2>&1 | sed 's/^/  /'
}
have_docker() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }

{
  sec "诊断时间"
  echo "  $(date -Is)  主机 $(uname -srm)"

  sec "宿主内存 / 共享内存 / 数据目录磁盘"
  grep -E '^(MemTotal|MemAvailable|CommitLimit|Committed_AS|SwapTotal|SwapFree):' /proc/meminfo | sed 's/^/  /'
  host df -h /dev/shm "$DATA_DIR"

  sec "宿主 dmesg：OOM / strata 相关记录"
  if dmesg -T >/dev/null 2>&1; then
    dmesg -T 2>/dev/null | grep -i -E 'killed process|out of memory|oom-kill|oom_reaper|strata' | tail -40 | sed 's/^/  /'
  else
    echo "  读不了 dmesg（需要 root）：sudo dmesg -T | grep -i -E 'killed process|out of memory|oom' | tail -40"
  fi

  if have_docker; then
    sec "compose 声明的限制（mem_limit / shm_size / deploy；缺省表示没设）"
    if command -v jq >/dev/null 2>&1; then
      docker compose config --format json 2>/dev/null \
        | jq -c '.services.strata | {mem_limit, memswap_limit, shm_size, oom_kill_disable, ulimits, deploy}' 2>&1 | sed 's/^/  /'
      docker compose config --format json 2>/dev/null \
        | jq -c '.services["idle-unload"] | {mem_limit, shm_size, ulimits}' 2>&1 | sed 's/^/  /'
    else
      echo "  没有 jq（应由 pixi 提供）：跳过"
    fi

    sec "实际容器的限制（Memory=0 表示不限制；ShmSize 单位字节）"
    if docker inspect qwen-strata >/dev/null 2>&1; then
      docker inspect qwen-strata 2>/dev/null \
        | jq -r '.[0].HostConfig | {Memory, MemorySwap, MemoryReservation, OomKillDisable, ShmSize, CgroupParent, Ulimits, NanoCpus}' 2>&1 | sed 's/^/  /'
      echo "  容器状态：$(docker inspect -f '{{.State.Status}} restarts={{.RestartCount}} oomkilled={{.State.OOMKilled}}' qwen-strata 2>&1)"
    else
      echo "  没有 qwen-strata 容器（还没启动？）"
    fi

    sec "容器状态"
    compose ps

    sec "服务日志（最后 500 行）"
    compose logs --no-color --tail=500 strata

    sec "关键行：引擎启动 / 退出码 / 重载 / OOM"
    docker compose logs --no-color --tail=500 strata 2>&1 \
      | grep -E "engine stopped|engine started|engine had stopped|loading the model again|out of memory|CUDA|killed|Its log:" \
      | tail -40 | sed 's/^/  /'

    if docker compose ps --status running --services 2>/dev/null | grep -qx strata; then
      sec "引擎日志（容器内 /data 下最近修改的 .log）"
      cont 'ls -lt /data/state 2>/dev/null | head -10; for f in $(ls -t /data/state/*.log /data/state/*/*.log /data/Strata-data/state/*.log 2>/dev/null | head -3); do echo "--- $f"; tail -n 120 "$f"; done'

      sec "容器内 ulimit（RTTIME / MEMLOCK / NOFILE）"
      cont 'ulimit -a'

      sec "容器内磁盘与 /dev/shm"
      cont 'df -h /data /dev/shm /tmp 2>/dev/null'

      sec "容器内 GPU"
      cont 'nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv 2>&1 | head -5'

      sec "strata launch --status"
      cont 'strata launch --status 2>&1 | head -25'
    else
      sec "容器未运行"
      echo "  strata 容器没有在运行：跳过容器内采集"
    fi
  else
    sec "docker 不可达"
    echo "  docker daemon 不可达：跳过容器 / 服务日志部分（宿主信息已收集）"
  fi

  sec "怎么读这份输出"
  cat <<'TIP'
  1. 服务日志里 `the engine stopped ... (exit code N)` 的 N 是引擎的退出码 / 信号：
     -9 = SIGKILL（内核 OOM killer 或 systemd-oomd；对应 dmesg 里的 killed process）
     -4 = SIGILL（CPU 缺少 AVX2 等指令）、-7 = SIGBUS（mmap / tmpfs 被截断）、-11 = SIGSEGV。
  2. 「engine started」之后没有任何输出就退出 = 引擎在写日志前就被结束了；先看 N，不要只看 503 文案。
  3. 容器内存应为 0（不限制，上游也明确「不要给容器设内存限制」）；不是 0 说明被额外设过 --memory。
  4. 把本文件（尤其是上面「关键行」与「引擎日志」两段）贴回 issue。
TIP
} >"$FILE" 2>&1

echo "诊断输出：$FILE"
if [ "$PRINT" = 1 ]; then
  echo
  cat "$FILE"
fi
exit 0
