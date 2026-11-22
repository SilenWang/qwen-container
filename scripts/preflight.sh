#!/usr/bin/env bash
# 环境自检：docker / GPU 运行时 / 驱动版本 / CPU / 内存 / 磁盘 / 端口 / 已下载的模型 / compose 渲染结果。
# 出问题先跑这个，比直接看容器日志快。
#
#   pixi run preflight
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
# shellcheck source=lib.sh
. "$HERE/scripts/lib.sh"
load_env "$HERE"

fails=0
warns=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; warns=$((warns + 1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; fails=$((fails + 1)); }

# ver_ge A B：A >= B 时返回 0（按版本号比较，如 580.65.06 vs 580）
ver_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]; }

STRATA_IMAGE="${STRATA_IMAGE:-ghcr.io/niko1221/strata:0.2.19-cuda13}"
STRATA_PORT="${STRATA_PORT:-8080}"
STRATA_BIND_ADDR="${STRATA_BIND_ADDR:-127.0.0.1}"
STRATA_FAMILY="${STRATA_FAMILY:-qwen}"
STRATA_MODEL="${STRATA_MODEL:-IQ2_XS}"
STRATA_PRESET="${STRATA_PRESET:-flash-next}"
STRATA_CONTEXT="${STRATA_CONTEXT:-128k}"
STRATA_KV="${STRATA_KV:-int8}"
STRATA_IDLE_UNLOAD_S="${STRATA_IDLE_UNLOAD_S:-1800}"
STRATA_API_KEY="${STRATA_API_KEY:-}"
DATA_DIR="${STRATA_DATA_DIR:-$HERE/strata-data}"

echo "== 工具链 =="
command -v pixi >/dev/null && ok "pixi $(pixi --version 2>/dev/null | head -1)" || bad "缺少 pixi"
command -v docker >/dev/null && ok "docker $(docker --version | awk '{print $3}' | tr -d ,)" || bad "缺少 docker"
docker compose version >/dev/null 2>&1 && ok "docker compose $(docker compose version --short)" || bad "缺少 docker compose 插件"
docker info >/dev/null 2>&1 && ok "docker daemon 可达" || bad "docker daemon 不可达"

echo
echo "== GPU 与驱动 =="
driver=""
if command -v nvidia-smi >/dev/null 2>&1; then
  if gpu="$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>&1 | head -1)" \
     && printf '%s' "$gpu" | grep -q 'MiB\|GiB'; then
    ok "宿主 GPU：$gpu"
    vram_mib="$(printf '%s' "$gpu" | awk -F', ' '{print $2}' | tr -dc '0-9')"
    case "$STRATA_FAMILY" in
      hy4-flash)      vram_floor=8000 ;;
      inkling-2-mini) vram_floor=4000 ;;
      *)              vram_floor=12000 ;;
    esac
    if [ "${vram_mib:-0}" -lt "$vram_floor" ]; then
      warn "显存 ${vram_mib} MiB：$STRATA_FAMILY 建议 ${vram_floor} MiB 起，小卡请换更小的档位"
    else
      ok "显存 ${vram_mib} MiB（$STRATA_FAMILY 建议 ${vram_floor} MiB 起）"
    fi
    driver="$(printf '%s' "$gpu" | awk -F', ' '{print $3}' | tr -dc '0-9.')"
  else
    warn "nvidia-smi 执行失败：$(printf '%s' "$gpu" | head -1)"
    warn "  → 驱动没装好，或你是在没有 GPU 的机器上跑 docker"
  fi
else
  warn "宿主没有 nvidia-smi（如果在别的机器上跑 docker，忽略此项）"
fi

case "$STRATA_IMAGE" in
  *cuda13*) need_driver=580 ;;
  *cuda12*) need_driver=575 ;;
  *)        need_driver="" ;;
esac
if [ -n "$need_driver" ]; then
  if [ -n "$driver" ] && ver_ge "$driver" "$need_driver"; then
    ok "驱动 $driver 满足 $STRATA_IMAGE 的要求（>= $need_driver）"
  elif [ -n "$driver" ]; then
    bad "驱动 $driver 低于 $STRATA_IMAGE 要求的 $need_driver → 升级驱动，或换成另一档镜像"
  else
    warn "读不到驱动版本；$STRATA_IMAGE 需要 $need_driver 以上"
  fi
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
grep -qw avx2 /proc/cpuinfo 2>/dev/null && ok "CPU 支持 AVX2（引擎按 AVX2 编译）" \
                                        || bad "CPU 不支持 AVX2：引擎可能以 illegal instruction 退出"
mem_gib="$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo)"
awk '/MemTotal/{printf "  内存总量：%.1f GiB\n", $2/1048576}' /proc/meminfo

# 各档位启动时要放进内存的专家权重 + 下载体积（GB），每个家族一张表。
# ram_reserve：Qwen 两档的 need_ram 只是权重侧占用，另留 8 GiB 系统余量；
# Hy4-Flash / Inkling 的官方数字已是整机内存门槛，不再加余量。
ram_reserve=8
case "$STRATA_FAMILY" in
  qwen36)
    case "$(printf '%s' "$STRATA_MODEL" | tr 'a-z' 'A-Z')" in
      UD-Q4_K_XL) need_ram=32; need_disk=23 ;;
      UD-Q6_K_XL) need_ram=44; need_disk=33 ;;
      Q8_0)       need_ram=52; need_disk=38 ;;
      *)          need_ram=0;  need_disk=0 ;;
    esac
    ;;
  hy4-flash)
    # 数字就是官方给的整机内存门槛；下载含 67.6 GB Engram（+0.93 GB Vision）
    ram_reserve=0
    case "$(printf '%s' "$STRATA_MODEL" | tr 'a-z' 'A-Z')" in
      SQ-64)  need_ram=64;  need_disk=130 ;;
      SQ-96)  need_ram=96;  need_disk=157 ;;
      SQ-128) need_ram=128; need_disk=181 ;;
      *)      need_ram=0;   need_disk=0 ;;
    esac
    ;;
  inkling-2-mini)
    # 12 GiB 卡跑 SQ-10 是整份放进显存；SQ-15/SQ-20 需要更大的卡或统一内存
    ram_reserve=0
    case "$(printf '%s' "$STRATA_MODEL" | tr 'a-z' 'A-Z')" in
      SQ-10) need_ram=12; need_disk=11 ;;
      SQ-15) need_ram=24; need_disk=15 ;;
      SQ-20) need_ram=32; need_disk=20 ;;
      *)     need_ram=0;  need_disk=0 ;;
    esac
    ;;
  *)
    case "$(printf '%s' "$STRATA_MODEL" | tr 'a-z' 'A-Z')" in
      Q2_0)    need_ram=44; need_disk=66 ;;
      IQ2_XS)  need_ram=46; need_disk=68 ;;
      IQ3_XXS) need_ram=53; need_disk=76 ;;
      IQ3_S)   need_ram=60; need_disk=84 ;;
      IQ1_M)   need_ram=33; need_disk=0 ;;
      *)       need_ram=0;  need_disk=0 ;;
    esac
    ;;
esac

if [ "$need_ram" -gt 0 ]; then
  if [ "$mem_gib" -ge "$((need_ram + ram_reserve))" ]; then
    ok "内存 ${mem_gib} GiB 满足 $STRATA_MODEL（约需 ${need_ram} GiB$([ "$ram_reserve" -gt 0 ] && printf ' + 系统余量')）"
  elif [ "$mem_gib" -ge "$need_ram" ]; then
    warn "内存 ${mem_gib} GiB 跑 $STRATA_MODEL 偏紧（约需 ${need_ram} GiB + 系统余量）"
  else
    bad "内存 ${mem_gib} GiB 不够 $STRATA_MODEL（约需 ${need_ram} GiB）→ 换小档或加内存"
  fi
else
  warn "STRATA_MODEL=$STRATA_MODEL：不在已知档位表里，跳过内存核算（空值表示让 Strata 按显卡自选）"
fi

mkdir -p "$DATA_DIR" 2>/dev/null || true
avail="$(df -BG --output=avail "$DATA_DIR" 2>/dev/null | tail -1 | tr -dc '0-9')"
avail="${avail:-0}"
if [ "$need_disk" -gt 0 ]; then
  if [ "$avail" -ge "$((need_disk + 20))" ]; then
    ok "数据目录所在磁盘剩余 ${avail}G（$STRATA_MODEL 约需 ${need_disk} GB）"
  elif [ "$avail" -ge "$need_disk" ]; then
    warn "数据目录剩余 ${avail}G，$STRATA_MODEL 占约 ${need_disk} GB，余量不多"
  else
    bad "数据目录剩余 ${avail}G 不够：$STRATA_MODEL 要下载约 ${need_disk} GB"
  fi
else
  warn "未知档位的下载体积，跳过磁盘核算（剩余 ${avail}G）"
fi
rot="$(lsblk -no ROTA "$(df --output=source "$DATA_DIR" | tail -1)" 2>/dev/null | head -1 | tr -dc '0-9')"
[ "${rot:-1}" = "0" ] && ok "数据目录在 SSD/NVMe 上" \
                      || warn "数据目录可能不在 SSD 上：专家权重会按需从磁盘读取，机械盘会严重拖慢"

echo
echo "== 配置与端口 =="
[ -f .env ] && ok ".env 存在" || warn ".env 不存在，将使用 docker-compose.yml 里的默认值（建议 cp .env.example .env）"
echo "    镜像     = $STRATA_IMAGE"
echo "    模型     = family=$STRATA_FAMILY model=$STRATA_MODEL preset=$STRATA_PRESET"
echo "    上下文   = $STRATA_CONTEXT   KV = ${STRATA_KV:-int8}"
echo "    空闲释放 = $(if [ "${STRATA_IDLE_UNLOAD_S}" = "0" ]; then printf '关闭'; else printf '%ss 无请求后卸载模型' "$STRATA_IDLE_UNLOAD_S"; fi)"
echo "    数据目录 = $DATA_DIR"
echo "    API key  = $(if [ -n "${STRATA_API_KEY:-}" ]; then printf '已设置（%d 字符）' "${#STRATA_API_KEY}"; else printf '未设置：首启自动生成，pixi run key 查看'; fi)"
if [ "${#STRATA_API_KEY}" -lt 16 ] && [ -n "${STRATA_API_KEY:-}" ]; then
  warn "STRATA_API_KEY 少于 16 字符，Strata 会警告；用 openssl rand -hex 24 生成"
fi

# 数据目录里已经下好哪些模型：有就是「启动即用、不再下载」
found_models=0
mdl_root="$DATA_DIR/Strata-data/models"
if [ -d "$mdl_root" ]; then
  for d in "$mdl_root"/*/; do
    [ -d "$d" ] || continue
    # Qwen 家族按档位存 GGUF 分片；Inkling / Hy4-Flash 按家族存 .strata 包
    n="$(find "$d" -maxdepth 1 -name '*.gguf' 2>/dev/null | wc -l)"
    if [ "$n" -gt 0 ]; then what="$n 个分片"
    else
      n="$(find "$d" -maxdepth 1 -name '*.strata' 2>/dev/null | wc -l)"
      what="$n 个 .strata 包"
    fi
    [ "$n" -gt 0 ] || continue
    found_models=1
    ok "已下载 $(basename "$d")：$what，$(du -sh "$d" 2>/dev/null | cut -f1) → 启动时不会再下载"
  done
fi
if [ "$found_models" = 0 ]; then
  warn "数据目录里还没有模型分片（$mdl_root）→ 首次启动会下载 $STRATA_MODEL"
fi

if bash -c "exec 3<>/dev/tcp/127.0.0.1/$STRATA_PORT" 2>/dev/null; then
  warn "端口 $STRATA_PORT 已被占用 → 容器会以「port is already allocated」启动失败，改 .env 里的 STRATA_PORT"
else
  ok "端口 $STRATA_PORT 未被占用"
fi

echo
echo "== 解析后的服务参数 =="
cfg_json="$(docker compose config --format json 2>/dev/null)"
if [ -n "$cfg_json" ]; then
  ok "docker compose config 校验通过"
  if command -v jq >/dev/null 2>&1; then
    echo "    image=$(printf '%s' "$cfg_json" | jq -r '.services.strata.image')"
    echo "    FAMILY=$(printf '%s' "$cfg_json" | jq -r '.services.strata.environment.FAMILY')  MODEL=$(printf '%s' "$cfg_json" | jq -r '.services.strata.environment.MODEL')  PRESET=$(printf '%s' "$cfg_json" | jq -r '.services.strata.environment.PRESET')"
    echo "    CONTEXT=$(printf '%s' "$cfg_json" | jq -r '.services.strata.environment.CONTEXT')  KV=$(printf '%s' "$cfg_json" | jq -r '.services.strata.environment.KV')  VISION=$(printf '%s' "$cfg_json" | jq -r '.services.strata.environment.VISION')"
    echo "    端口 $(printf '%s' "$cfg_json" | jq -r '.services.strata.ports[0] | "\(.host_ip):\(.published)->\(.target)"')"
    echo "    数据目录=$(printf '%s' "$cfg_json" | jq -r '.services.strata.volumes[] | select(.target == "/data") | .source')"
  else
    warn "缺少 jq（应由 pixi 环境提供），跳过参数回显"
  fi
else
  bad "docker compose config 校验失败："
  docker compose config 2>&1 | sed 's/^/    /' | head -10
fi

# 容器在跑的时候，如实说明它加载的是哪个模型（.env 改过但没 recreate 的常见坑）
if docker info >/dev/null 2>&1; then
  running="$(docker compose ps --status running --services 2>/dev/null | tr '\n' ' ')"
  case "$running" in
    *strata*) ok "strata 容器已在运行（改完 .env 用 pixi run recreate 生效）" ;;
  esac
fi

echo
if [ "$fails" -gt 0 ]; then
  printf '\033[31m自检失败：%d 项错误，%d 项警告\033[0m\n' "$fails" "$warns"
  exit 1
fi
printf '\033[32m自检通过\033[0m（%d 项警告）\n' "$warns"
