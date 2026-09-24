# shellcheck shell=bash
# 项目内 bash 脚本共用的小工具。
#
# load_env <项目根目录>
#   读取 .env，但**不覆盖已存在的环境变量** —— 这样 `MODEL_FILE=xxx pixi run download`
#   这种一次性覆盖才不会被 .env 里的值顶掉。
load_env() {
  local root="$1" file line key value
  file="$root/.env"
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in '' | '#'*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key="${line%%=*}"
    value="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"
    [ -n "$key" ] || continue
    [ -n "${!key:-}" ] && continue
    export "$key=$value"
  done < "$file"
}