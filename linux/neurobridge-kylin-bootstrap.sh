#!/usr/bin/env bash
# Network-free startup entry for a complete NeuroBridge checkout on Galaxy
# Kylin. Older checkouts must first receive the generated one-file offline
# update runner; startup itself never fetches or pulls source code.
set -u -o pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
if [[ -f $script_dir/pyproject.toml ]]; then
  project_dir=$script_dir
elif [[ -f $script_dir/../pyproject.toml ]]; then
  project_dir=$(cd "$script_dir/.." && pwd -P)
elif [[ -f $PWD/pyproject.toml ]]; then
  project_dir=$(pwd -P)
else
  project_dir=$script_dir
fi
assistant="$project_dir/linux/setup-kylin-gateway.sh"
bootstrap_log=

usage() {
  cat <<'EOF'
Usage: bash neurobridge-kylin-bootstrap.sh

Run this file from linux/ in a complete NeuroBridge checkout as the normal
desktop user. It verifies the project runtime directory and opens the numeric
gateway menu. Daily startup does not inspect or change Git ownership.

Startup never runs git fetch or git pull. To update an old/offline computer,
generate neurobridge-kylin-offline-update.run on a development computer,
transfer that single file to the project root, and run it once.

Do not run with sudo. The script requests sudo only for ownership repair; all
gateway commands run as the normal user.
EOF
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

ask_yes_no() {
  local prompt=$1 answer
  while true; do
    printf '%s [yes/no]: ' "$prompt"
    IFS= read -r answer || return 1
    case $answer in
      [Yy][Ee][Ss]|[Yy]) return 0 ;;
      [Nn][Oo]|[Nn]) return 1 ;;
      *) printf '请输入 yes 或 no。\n' ;;
    esac
  done
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  usage
  exit 0
fi
[[ $# -eq 0 ]] || fail "Unknown option: $1"
[[ ${EUID:-$(id -u)} -ne 0 ]] || fail \
  "不要使用 sudo；请直接执行：bash neurobridge-kylin-bootstrap.sh"

[[ -n $project_dir && $project_dir != / ]] || fail "不安全的项目目录：$project_dir"
[[ -f $project_dir/pyproject.toml ]] || fail \
  "请把本文件放入 NeuroBridge 项目根目录，或从项目 linux/ 目录运行。"
[[ -d $project_dir/.git && ! -L $project_dir/.git ]] || fail \
  "当前目录不是可安全修复的 NeuroBridge Git 项目：$project_dir"

current_uid=$(id -u)
current_gid=$(id -g)
runtime_dir="$project_dir/.runtime"
[[ ! -L $runtime_dir ]] || fail ".runtime 不能是符号链接。"
[[ ! -L $runtime_dir/logs ]] || fail ".runtime/logs 不能是符号链接。"
runtime_probe=
if mkdir -p -- "$runtime_dir/logs" 2>/dev/null \
  && [[ -d $runtime_dir/logs && ! -L $runtime_dir/logs ]]; then
  runtime_probe=$(mktemp "$runtime_dir/logs/.bootstrap-write.XXXXXX" 2>/dev/null || true)
fi
if [[ -z $runtime_probe ]]; then
  printf '项目运行目录不可写；日常启动不需要修复 Git。\n'
  ask_yes_no "是否只修复 .runtime 运行目录权限？" || fail "已取消；项目未修改。"
  sudo install -d -o "$current_uid" -g "$current_gid" -m 0750 \
    "$runtime_dir" "$runtime_dir/logs" || fail "运行目录创建失败。"
  sudo chown -R --no-dereference "$current_uid:$current_gid" "$runtime_dir" \
    || fail "运行目录权限修复失败。"
else
  rm -f -- "$runtime_probe"
fi
bootstrap_log="$project_dir/.runtime/logs/kylin-bootstrap-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
touch "$bootstrap_log" || fail "无法创建引导日志。"
chmod 0600 "$bootstrap_log" 2>/dev/null || true
exec > >(tee -a "$bootstrap_log") 2>&1
printf 'project=%s\nlog=%s\nsourceUpdateAttempted=false\n' "$project_dir" "$bootstrap_log"
printf 'gitPermissionChecked=false\n'

if [[ ! -f $assistant ]]; then
  fail "当前项目缺少 linux/setup-kylin-gateway.sh；启动入口不会自动执行 Git。请把开发机生成的 neurobridge-kylin-offline-update.run 传到项目根目录，执行 bash neurobridge-kylin-offline-update.run，更新完成后会自动打开菜单。"
fi

printf '正在打开 NeuroBridge 银河麒麟数字菜单……\n'
exec bash "$assistant"
