#!/usr/bin/env bash
# 下载 Qwen3.6-35B-A3B 的 GGUF 权重（默认 UD-Q4_K_XL，约 22.4 GB）
#
#   pixi run download
#   MODEL_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf pixi run download   # 换量化
#   MMPROJ_FILE=mmproj-F16.gguf pixi run download                 # 额外下视觉投影
#
# 支持断点续传；下完会核对文件大小（与 HF 上声明的一致才算成功）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$HERE/scripts/lib.sh"
load_env "$HERE"

MODEL_REPO="${MODEL_REPO:-unsloth/Qwen3.6-35B-A3B-GGUF}"
MODEL_FILE="${MODEL_FILE:-Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf}"
MODELS_DIR="$HERE/models"
mkdir -p "$MODELS_DIR"

hf_url() { printf 'https://huggingface.co/%s/resolve/main/%s' "$1" "$2"; }

# 取 HF 上声明的文件大小（LFS 文件走 x-linked-size）
remote_size() {
  curl -fsIL --retry 3 "$1" 2>/dev/null | tr -d '\r' | awk '
    tolower($1) == "x-linked-size:"   { v = $2 }
    tolower($1) == "content-length:"  { v = $2 }
    END { print v }'
}

download() {
  local repo="$1" file="$2" url dest expected actual
  url="$(hf_url "$repo" "$file")"
  dest="$MODELS_DIR/$file"
  mkdir -p "$(dirname "$dest")"

  expected="$(remote_size "$url" || true)"
  if [ -n "$expected" ] && [ -f "$dest" ] && [ "$(stat -c %s "$dest")" = "$expected" ]; then
    echo "已存在且大小正确，跳过：$file ($(numfmt --to=iec "$expected"))"
    return 0
  fi

  echo "=== 下载 $repo/$file ==="
  [ -n "$expected" ] && echo "    大小 $(numfmt --to=iec "$expected")"
  curl -fL --retry 5 --retry-delay 3 -C - -o "$dest" "$url"

  if [ -n "$expected" ]; then
    actual="$(stat -c %s "$dest")"
    if [ "$actual" != "$expected" ]; then
      echo "!! 大小不符：本地 $actual != 远端 $expected，文件可能不完整" >&2
      return 1
    fi
  fi
  echo "    OK：$dest"
}

download "$MODEL_REPO" "$MODEL_FILE"

if [ -n "${MMPROJ_FILE:-}" ]; then
  download "$MODEL_REPO" "$MMPROJ_FILE"
fi

echo
echo "===== 完成 ====="
ls -lh "$MODELS_DIR"
echo
echo "下一步：pixi run up && pixi run smoke"