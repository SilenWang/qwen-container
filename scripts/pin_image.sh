#!/usr/bin/env bash
# 打印当前 llama.cpp 镜像的 digest，便于把 .env 里的 tag 换成不可变的 digest。
#
#   pixi run pin-image
#
# 输出示例：把 .env 改成
#   LLAMA_CPP_IMAGE=ghcr.io/ggml-org/llama.cpp@sha256:....
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$HERE/scripts/lib.sh"
load_env "$HERE"

IMAGE="${LLAMA_CPP_IMAGE:-ghcr.io/ggml-org/llama.cpp:server-cuda-b11146}"
echo "镜像：$IMAGE"
echo

echo "== 拉取（若本地已有则跳过）=="
docker pull -q "$IMAGE"

echo
echo "== 本地 RepoDigests =="
docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$IMAGE"

echo "== 远端 manifest digest =="
if docker buildx imagetools inspect "$IMAGE" --format '{{.Manifest.Digest}}' 2>/dev/null; then
  :
else
  echo "（buildx 不可用，跳过；上面的 RepoDigests 已足够用于锁定）"
fi

echo
echo "锁定方式：在 .env 中写入"
echo "  LLAMA_CPP_IMAGE=ghcr.io/ggml-org/llama.cpp@sha256:<上面任一 digest>"
echo "然后 pixi run recreate"