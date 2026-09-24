#!/usr/bin/env bash
# 环境自检：docker / GPU 运行时 / CPU / 内存 / 磁盘 / 模型文件 / 解析后的关键参数。
# 出问题先跑这个，比直接看容器日志快。
#
#   pixi run preflight
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

fails=0
warns=0
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; warns=$((warns + 1)); }
bad()   { printf '  \033[31m✗\033[0m %s\n' "$*"; fails=$((fails + 1)); }

echo "== 工具链 =="
command -v pixi >/dev/null && ok "pixi $(pixi --version 2>/dev/null | head -1)" || bad "缺少 pixi"
command -v docker >/dev/null && ok "docker $(docker --version | awk '{print $3}' | tr -d ,)" || bad "缺少 docker"
docker compose version >/dev/null 2>&1 && ok "docker compose $(docker compose version --short)" || bad "缺少 docker compose 插件"
docker info >/dev/null 2>&1 && ok "docker daemon 可达" || bad "docker daemon 不可达"
command -v jq >/dev/null && ok "jq 可用" || warn "缺少 jq（应由 pixi 环境提供）"

echo
echo "== GPU =="
if command -v nvidia-smi >/dev/null 2>&1; then
  if gpu="$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>&1 | head -1)" \
     && printf '%s' "$gpu" | grep -q 'MiB\|GiB'; then
    ok "宿主 GPU：$gpu"
  else
    warn "nvidia-smi 执行失败：$(printf '%s' "$gpu" | head -1)"
    warn "  → 驱动没装好，或你是在没有 GPU 的机器上跑 docker"
  fi
else
  warn "宿主没有 nvidia-smi（如果在别的机器上跑 docker，忽略此项）"
fi
runtimes="$(docker info --format '{{json .Runtimes}}' 2>/dev/null || echo '{}')"
if printf '%s' "$runtimes" | grep -q '"nvidia"'; then
  ok "docker 已注册 nvidia 运行时（nvidia-container-toolkit 就绪）"
else
  bad "docker 未注册 nvidia 运行时 → 容器看不到 GPU，请安装 nvidia-container-toolkit"
fi

echo
echo "== 宿主机资源 =="
ok "CPU 逻辑核：$(nproc)"
awk '/MemTotal/{printf "  内存总量：%.1f GiB\n", $2/1048576}' /proc/meminfo
mem_gib="$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo)"
[ "$mem_gib" -lt 32 ] && warn "系统内存 < 32 GiB：MoE 专家要放内存，建议 32 GiB 起步、48–64 GiB 更稳" \
                       || ok "内存满足专家卸载需求"
avail="$(df -BG --output=avail "$HERE" | tail -1 | tr -dc '0-9')"
[ "${avail:-0}" -lt 60 ] && warn "项目目录可用空间仅 ${avail}G，权重约 22 GB + 镜像若干 GB" \
                          || ok "项目目录可用空间 ${avail}G"

echo
echo "== 配置与模型 =="
if [ -f .env ]; then
  ok ".env 存在"
  # shellcheck source=lib.sh
  . "$HERE/scripts/lib.sh"
  load_env "$HERE"
else
  warn ".env 不存在，将使用 docker-compose.yml 里的默认值（建议 cp .env.example .env）"
fi
MODEL_FILE="${MODEL_FILE:-Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf}"
if [ -s "models/$MODEL_FILE" ]; then
  ok "模型文件 models/$MODEL_FILE：$(numfmt --to=iec "$(stat -c %s "models/$MODEL_FILE")")"
else
  bad "缺少模型文件 models/$MODEL_FILE → 先跑 pixi run download"
fi
[ -d cache ] && ok "cache/ 目录存在" || warn "cache/ 不存在（docker 会自动创建，建议先 mkdir -p cache）"

PORT_CFG="${PORT:-8080}"
if bash -c "exec 3<>/dev/tcp/127.0.0.1/$PORT_CFG" 2>/dev/null; then
  warn "端口 $PORT_CFG 已被占用 → 容器会以「port is already allocated」启动失败，改 .env 里的 PORT"
else
  ok "端口 $PORT_CFG 未被占用"
fi

echo
echo "== 解析后的服务参数 =="
if cfg="$(docker compose config 2>&1)"; then
  # 从渲染后的命令里取某个 flag 后面的值（值是 YAML 列表的下一项）
  arg() {
    printf '%s\n' "$cfg" | awk -v f="$1" '
      $2 == f { getline; gsub(/^[[:space:]]*-[[:space:]]*/, ""); gsub(/"/, ""); print; exit }'
  }
  ok "docker compose config 校验通过"
  echo "    --ctx-size $(arg --ctx-size)  --n-gpu-layers $(arg --n-gpu-layers)  --n-cpu-moe $(arg --n-cpu-moe)"
  echo "    --cache-type-k $(arg --cache-type-k)  --cache-type-v $(arg --cache-type-v)  --parallel $(arg --parallel)"
  ctk="$(arg --cache-type-k)"
  ctv="$(arg --cache-type-v)"
  if [ -n "$ctk" ] && [ "$ctk" != "$ctv" ]; then
    bad "K/V 量化类型不一致（$ctk vs $ctv）：CUDA FlashAttention 会静默回落到 CPU 后端"
  fi
else
  bad "docker compose config 校验失败："
  printf '%s\n' "$cfg" | sed 's/^/    /' | head -10
fi

echo
if [ "$fails" -gt 0 ]; then
  printf '\033[31m自检失败：%d 项错误，%d 项警告\033[0m\n' "$fails" "$warns"
  exit 1
fi
printf '\033[32m自检通过\033[0m（%d 项警告）\n' "$warns"