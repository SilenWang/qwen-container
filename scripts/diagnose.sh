#!/usr/bin/env bash
# 现场诊断：把「引擎连续退出 / 503 The engine stopped 3 times in 10 minutes」的判断依据一次收齐。
#
#   pixi run diagnose                 # 写到 output/diagnose-<时间>.txt
#   pixi run diagnose -- --print      # 同时打印到终端
#
# 收集：宿主内存 / /dev/shm / 数据目录磁盘、宿主 GPU 与 dmesg 的 OOM / Xid、compose 与实际容器的
# 内存限制、healthcheck 详情与最近事件、**全量**服务日志里的关键错误、精确路径
# /data/state/strata/launch/*.log 的引擎日志、容器内 ulimit / 磁盘 / GPU。
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
  sec "重要：复现 503 后先别重启 / 重建容器"
  echo "  下一次 launch（pixi run recreate / up / restart，或容器重启）会用新日志截断覆盖"
  echo "  /data/state/strata/launch/*.engine.log，旧容器的崩溃记录随之丢失。"
  echo "  复现后立刻跑本脚本并保存输出，再决定是否重建。"

  sec "诊断时间"
  echo "  $(date -Is)  主机 $(uname -srm)"

  sec "宿主内存 / 共享内存 / 数据目录磁盘"
  grep -E '^(MemTotal|MemAvailable|CommitLimit|Committed_AS|SwapTotal|SwapFree):' /proc/meminfo | sed 's/^/  /'
  host df -h /dev/shm "$DATA_DIR"

  sec "宿主 GPU（容器内 NVML 失败时，用它区分「GPU 还在」和「GPU / 驱动掉了」）"
  if command -v nvidia-smi >/dev/null 2>&1; then
    host nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version,pstate,temperature.gpu --format=csv
    host nvidia-smi -q 2>/dev/null | grep -i -E "Xid|ECC|Retired|Remapped|Pending|Thermal|Power Draw|Clocks Event" | head -40
  else
    echo "  宿主没有 nvidia-smi"
  fi

  sec "宿主 dmesg：OOM / Xid / strata 相关记录"
  if dmesg -T >/dev/null 2>&1; then
    dmesg -T 2>/dev/null \
      | grep -i -E 'killed process|out of memory|oom-kill|oom_reaper|nvidia|nvrm|xid|fallen off|strata' \
      | tail -60 | sed 's/^/  /'
  else
    echo "  读不了 dmesg（需要 root）。最小命令："
    echo "  sudo dmesg -T | grep -i -E 'killed process|out of memory|oom|NVRM|Xid|fallen off' | tail -60"
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
      if command -v jq >/dev/null 2>&1; then
        docker inspect qwen-strata 2>/dev/null \
          | jq -r '.[0].HostConfig | {Memory, MemorySwap, MemoryReservation, OomKillDisable, ShmSize, CgroupParent, Ulimits, NanoCpus}' 2>&1 | sed 's/^/  /'
      else
        echo "  没有 jq：只打印关键字段（直接 bash 运行、不在 pixi 环境时）"
        docker inspect -f 'Memory={{.HostConfig.Memory}} MemorySwap={{.HostConfig.MemorySwap}} OomKillDisable={{.HostConfig.OomKillDisable}} ShmSize={{.HostConfig.ShmSize}} NanoCpus={{.HostConfig.NanoCpus}}' qwen-strata 2>&1 | sed 's/^/  /'
      fi
      echo "  容器状态：$(docker inspect -f '{{.State.Status}} restarts={{.RestartCount}} oomkilled={{.State.OOMKilled}} started={{.State.StartedAt}}' qwen-strata 2>&1)"
    else
      echo "  没有 qwen-strata 容器（还没启动？）"
    fi

    sec "容器健康检查（Status / FailingStreak / 最近几次的 ExitCode 与输出）"
    docker inspect qwen-strata --format 'Status={{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}} FailingStreak={{if .State.Health}}{{.State.Health.FailingStreak}}{{else}}0{{end}}' 2>&1 | sed 's/^/  /'
    docker inspect qwen-strata --format '{{range .State.Health.Log}}exit={{.ExitCode}} start={{.Start}} output={{printf "%s" .Output}}{{"\n"}}{{end}}' 2>&1 | tail -8 | sed 's/^/  /'

    sec "最近 24 小时的容器事件（只取 die / oom / kill / destroy / restart；已按 event 过滤，不截断）"
    if command -v timeout >/dev/null 2>&1; then
      timeout 5 docker events --since 24h --filter container=qwen-strata \
        --filter event=die --filter event=oom --filter event=kill \
        --filter event=destroy --filter event=restart \
        --format '{{.Time}} {{.Action}} name={{.Actor.Attributes.name}} exit={{.Actor.Attributes.exitCode}}' 2>&1 \
        | sed 's/^/  /'
    else
      echo "  没有 timeout 命令；手动："
      echo "    docker events --since 24h --filter container=qwen-strata --filter event=die --filter event=oom --filter event=kill --filter event=destroy --filter event=restart"
    fi

    sec "容器状态"
    compose ps

    sec "服务日志（最后 500 行，看上下文）"
    compose logs --no-color --tail=500 strata

    sec "全量服务日志里的关键错误（grep 全部历史，不只看末尾 500 行）"
    docker compose logs --no-color strata 2>&1 \
      | grep -i -n -E "engine stopped|engine had stopped|engine started|loading the model again|crash|xid|cuda error|cuda_error|no cuda-capable|nvml|out of memory|killed|SIG[A-Z]+|panic|abort|fatal|failed to (start|load)|unhealthy" \
      | tail -200 | sed 's/^/  /'

    if docker compose ps --status running --services 2>/dev/null | grep -qx strata; then
      sec "引擎日志（精确路径 /data/state/strata/launch/*.log；启动横幅里写的那个文件）"
      cont 'ls -lt /data/state/strata/launch 2>/dev/null | head -20'
      cont 'for f in /data/state/strata/launch/*.log; do [ -f "$f" ] || continue; echo "--- $f"; tail -n 200 "$f"; done'

      sec "launch 配置（引擎参数，如 --ram-experts / --vram-experts-gb）"
      cont 'for f in /data/state/strata/launch/*.json; do [ -f "$f" ] || continue; echo "--- $f"; head -c 4000 "$f"; echo; done'

      sec "宿主侧同一份引擎日志（数据目录挂载在本地时）"
      if [ -d "$DATA_DIR/state/strata/launch" ]; then
        host ls -lt "$DATA_DIR/state/strata/launch"
        for f in "$DATA_DIR/state/strata/launch"/*.log; do
          [ -f "$f" ] || continue
          echo "  --- $f"
          host tail -n 200 "$f"
        done
      else
        echo "  宿主侧没有 $DATA_DIR/state/strata/launch（数据目录可能不在本地）"
      fi

      sec "容器内 ulimit（RTTIME / MEMLOCK / NOFILE）"
      cont 'ulimit -a'

      sec "容器内磁盘与 /dev/shm"
      cont 'df -h /data /dev/shm /tmp 2>/dev/null'

      sec "容器内 GPU（NVML 失败本身就是证据：GPU 句柄 / 驱动在容器里不可用）"
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
  1. 服务日志里的 `the engine stopped ... (exit code N)`：N 是引擎退出码 / 信号。
     -9 = SIGKILL（内核 OOM killer / systemd-oomd）、-4 = SIGILL（CPU 指令）、
     -7 = SIGBUS（mmap / tmpfs 被截断）、-11 = SIGSEGV、-6 = SIGABRT。
  2. 「engine started」之后没有任何输出就退出 = 引擎在写日志前就被结束了；先看 N。
     服务日志只保留容器 stdout；更早的记录看「全量服务日志」段与 /data/state/strata/launch/*.log。
  3. 「容器健康检查」段：Status / FailingStreak 与最近几条 ExitCode + 输出，能解释
     docker ps 显示 unhealthy 而 strata 说 ready 的矛盾（healthcheck 命令 / key / 端口）。
  4. 宿主 nvidia-smi 与 dmesg 的 NVRM / Xid：容器内 NVML 失败时，用它区分 GPU 还在还是掉了。
  5. 容器内存应为 0（不限制，上游也明确「不要给容器设内存限制」）；不是 0 说明被额外设过 --memory。
  6. 把本文件里「全量服务日志」「引擎日志」「容器健康检查」「宿主 GPU / dmesg」几段贴回 issue。
TIP
} >"$FILE" 2>&1

echo "诊断输出：$FILE"
if [ "$PRINT" = 1 ]; then
  echo
  cat "$FILE"
fi
exit 0
