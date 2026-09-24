#!/usr/bin/env bash
# （可选）自编译 llama.cpp CUDA 镜像。
#
# 什么时候需要它：
#   * 想用「K/V 不同类型」的量化组合（例如 -ctk q4_0 -ctv f16）。官方镜像只编译了
#     q4_0/q4_0、q8_0/q8_0、f16/f16 这几组 FlashAttention 内核，其它组合会**静默回落
#     到 CPU 后端**（prefill 掉 20–30 倍）。加 -DGGML_CUDA_FA_ALL_QUANTS=ON 可覆盖全部组合。
#   * 想固定到某个 commit，或自己加补丁。
#
# 默认配置（q8_0/q8_0）用官方镜像就够了，不需要跑这个脚本。
#
#   pixi run build-image
#   CUDA_DOCKER_ARCH=89 IMAGE_TAG=qwen36-llamacpp:cuda pixi run build-image   # RTX 40 系
#
# 编译耗时视机器而定（十几分钟到一小时），且需要能拉取 nvidia/cuda devel 镜像。
set -euo pipefail

# 与 .env 默认镜像的 build 号一致（见 pin-image）
LLAMA_CPP_REF="${LLAMA_CPP_REF:-b11146}"
IMAGE_TAG="${IMAGE_TAG:-qwen36-llamacpp:cuda}"
# 86=RTX 30 系 / 89=RTX 40 系 / 120=RTX 50 系
CUDA_DOCKER_ARCH="${CUDA_DOCKER_ARCH:-86}"

command -v curl >/dev/null || { echo "缺少 curl" >&2; exit 1; }
command -v docker >/dev/null || { echo "缺少 docker" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "== 1/3 下载 llama.cpp@$LLAMA_CPP_REF 源码 =="
# 用源码包而不是 git clone：少一个依赖，也不受浅克隆/代理影响
curl -fL --retry 3 -o "$WORK/llama.tar.gz" \
  "https://codeload.github.com/ggml-org/llama.cpp/tar.gz/$LLAMA_CPP_REF"
tar xzf "$WORK/llama.tar.gz" -C "$WORK"
SRC="$WORK/llama.cpp-$LLAMA_CPP_REF"
[ -d "$SRC" ] || SRC="$(find "$WORK" -maxdepth 1 -type d -name 'llama.cpp-*' | head -1)"
[ -n "$SRC" ] && [ -d "$SRC" ] || { echo "解包后找不到源码目录" >&2; exit 1; }
echo "  源码目录：$SRC"

DOCKERFILE="$SRC/.devops/cuda.Dockerfile"
[ -f "$DOCKERFILE" ] || { echo "找不到 $DOCKERFILE（上游改了目录结构？）" >&2; exit 1; }

echo "== 2/3 打补丁：开启 GGML_CUDA_FA_ALL_QUANTS =="
if grep -q 'GGML_CUDA_FA_ALL_QUANTS' "$DOCKERFILE"; then
  echo "  已有该选项，跳过"
else
  sed -i 's/-DGGML_CUDA=ON/-DGGML_CUDA=ON -DGGML_CUDA_FA_ALL_QUANTS=ON/' "$DOCKERFILE"
  grep -q 'GGML_CUDA_FA_ALL_QUANTS' "$DOCKERFILE" \
    || { echo "补丁未生效：上游 cmake 命令行可能变了，请手动检查 $DOCKERFILE" >&2; exit 1; }
  echo "  已插入 -DGGML_CUDA_FA_ALL_QUANTS=ON"
fi

echo "== 3/3 docker build（target=server，CUDA_DOCKER_ARCH=$CUDA_DOCKER_ARCH）=="
docker build \
  --target server \
  --build-arg "CUDA_DOCKER_ARCH=$CUDA_DOCKER_ARCH" \
  -t "$IMAGE_TAG" \
  -f "$DOCKERFILE" \
  "$SRC"

echo
echo "完成：$IMAGE_TAG"
echo "在 .env 中设置 LLAMA_CPP_IMAGE=$IMAGE_TAG，然后："
echo "  pixi run recreate && pixi run smoke"